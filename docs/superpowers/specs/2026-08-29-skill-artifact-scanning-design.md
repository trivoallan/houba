# Spec: scanning skill artifacts with SkillSpector in the existing scan pipeline

Status: **Draft**
Date: 2026-08-29
Relates to: [0042](../../architecture/decisions/0042-platform-scan-pipeline-incremental-reconcile-fed.md)
(scan pipeline), [0043](../../architecture/decisions/0043-knock-scan-command-group-optional-extra.md)
(`knock scan` extra), [0039](../../architecture/decisions/0039-sarif-kind-discriminates-policy-from-vuln.md)
(SARIF kind), [0027](../../architecture/decisions/0027-sarif-finding-type.md) (SARIF finding type),
[0001](../../architecture/decisions/0001-mirror-policy-format.md) (standard keys for standard facts)
ADR: 0047 (proposed) — non-registry source and the `skill` artifact class
Depends on: the external-skill intake work (CEO plan `2026-08-29-external-skill-intake`), which
introduces the `skill` artifact class, the git source, and the `knock manifest` projection.

## Summary

External agent skills entering the registry need a content verdict before they reach a developer
workstation. [SkillSpector](https://github.com/nvidia/skillspector) (NVIDIA) already does exactly
that job — prompt injection, data exfiltration, supply-chain risk in Claude Code / Codex / MCP
skills — it accepts a **zip** as input and it emits **SARIF**.

knock already consumes SARIF: `BUILTIN_FORMATS = (SarifMapper(),)`, `knock attach` turns a report
into a signed OCI referrer, and `gate_breached(facts, fail_on)` is a pure, tested severity gate.
The scan pipeline worker loop is already `regis scan → knock attach → publish → ack` (ADR 0042).

So SkillSpector is **not** an addition to the chain. It slots in exactly where `regis scan` already
sits, for artifacts whose manifest `artifactType` is the skill media type. No new pipeline, no new report format,
no new protocol. ADR 0042's "reconcile does not scan inline (keeps knock ≠ scanner)" still holds:
SkillSpector is external glue, exactly like regis.

The single real difference in kind: **`regis scan` takes a registry reference, SkillSpector takes a
zip.** The worker must materialise the blob to a file before invoking it.

## Non-goals

- **A Harbor pluggable-scanner-spec adapter.** Considered and deferred. It would surface findings
  natively in the Harbor UI and be reusable by any organisation, but it does not unblock the
  internal gate any faster, it needs a service to host and certify, and it rests on an unverified
  assumption: whether Harbor dispatches a scan for a non-image artifact at all. Revisit once the
  internal gate is live and there is a real use case to show upstream.
- **Teaching regis to target non-image artifacts.** That is the preferred long-term shape (see
  *Trajectory*), but its effort is unscoped and the gate is needed before it lands.
- **knock forming an opinion on skill content.** knock stamps facts. SkillSpector produces the
  finding; the severity gate is a pure function over annotations.

## Architecture

```
  reconcile places the skill artifact ──▶ out_digest ──▶ Redis queue      (all existing)
                                                             │
                                                             ▼
                                                    scan worker (existing)
                                                             │
                        ┌────────────────────────────────────┴───────────────┐
                        │                                                    │
      manifest artifactType != skill                  manifest artifactType == skill
                        │                                                    │
                 regis scan <ref>                    materialise blob ──▶ /tmp/<digest>.zip
                        │                                                    │
                        │                     ┌──────────────────────────────┴──────────┐
                        │                     │                                         │
                        │        skillspector scan x.zip              skillspector scan x.zip
                        │        --no-llm --format sarif              --format sarif    (LLM)
                        │                     │                                         │
                        │             GATING report                          DOSSIER report
                        │             scan.tool=skillspector-static  scan.tool=skillspector-llm
                        ▼                     ▼                                         ▼
              ┌──────────────────────────────────────────────────────────────────────────────┐
              │   knock attach <ref> --report <sarif>  →  signed OCI referrer   (unchanged)   │
              └──────────────────────────────────────────────────────────────────────────────┘
                                              │ ack
                                              ▼
              knock manifest — projects the registry; includes an artifact ONLY IF its gating
                               referrer exists AND passes the threshold          (fail closed)
                                              │
                                              ▼
                digest-pinned manifest → managed settings → developer workstations
```

### Three structural choices

**Two reports, not one.** The static report carries the blocking verdict; the LLM report is
evidence for the human reviewer. They are distinguished by `scan.tool`. `knock gc` keeps the newest
per `(tool, format)`, so both survive without evicting each other — **`gc` needs no change**.

Rationale for keeping the LLM out of the gate: an LLM verdict is non-deterministic, so the same
artifact could pass today and fail tomorrow on a digest-pinned trust root; it costs a model call on
every re-intake; and it means asking a model to read content specifically engineered to manipulate
models. A scanner that can be talked out of reporting is the same attack class it is looking for.

**Fail closed.** An artifact with no gating referrer is not included in the manifest. Absence of a
verdict is not a verdict. This is *coverage gates value* applied at the projection.

**Size gate bound.** SkillSpector caps ingestion at `INGEST_MAX_BYTES` (100 MiB). A skill above that
can never be judged, therefore can never enter the manifest. The packaging size gate introduced by
the intake work must therefore be **≤ 100 MiB** — which finally gives that threshold a justification
instead of an arbitrary value.

## Components

| Component | Nature | Responsibility |
|---|---|---|
| `ImageInfo.artifact_type` | extension | New optional field with a default, populated by the regctl adapter from the manifest's standard `artifactType`. This is what the worker dispatches on. |
| `ports/registry.py`: blob read | **new** | `get_blob(ref, digest) -> bytes`. The port currently has `copy`, `annotate`, `put_referrer`, `inspect` — nothing reads a blob. Symmetric to the standalone-artifact **write** method the intake work adds. |
| `adapters/skillspector_cli.py` | **new** | Subprocess adapter, same shape as `adapters/syft_cli.py`. Two invocations: static (gating) and LLM (dossier). Returns two SARIF paths. |
| scan worker: dispatch | extension | manifest `artifactType == application/vnd.knock.skill.v1` → SkillSpector, else → regis. The rest of the loop is untouched. |
| `knock attach` | **unchanged** | Invoked twice, `--format sarif`. |
| `knock gc` | **unchanged** | Keeps newest per `(tool, format)`; both reports coexist. |
| `knock manifest`: verdict filter | extension | `gate_breached(referrer.annotations, fail_on)` — the existing pure function. **No SARIF is re-parsed at projection time**; the referrer already carries the `scan.*` facts written by `build_scan_annotations`. |
| severity threshold | config | Policy-level field with a fleet default, following the `KNOCK_RETENTION` pattern. |

### Why `ImageInfo.artifact_type` and not an annotation

An earlier draft carried the kind in an `io.knock.artifact.kind` annotation to avoid touching the
port. That was wrong on three counts:

1. ADR 0001 reserves `io.knock.*` for novel facts and mandates standard keys for standard facts.
   Artifact type is a standard fact with a standard OCI field.
2. `ImageInfo` is a frozen dataclass that **already** has a defaulted field (`config_labels`).
   Adding `artifact_type: str | None = None` is additive and breaks no consumer.
3. The dispatch decides *which scanner runs*. A missing annotation makes a skill look like an image,
   runs regis on it, and produces an empty report that **passes the gate** — a silent failure on the
   exact path this work exists to protect. A typed field read from the manifest cannot be forgotten
   at write time; an annotation can.

### Two vocabularies, one mapping — do not conflate them

`artifactType` means two different things in this system and the spec must be explicit about which
one the worker reads:

- **Policy vocabulary.** `MirrorPolicy.spec.artifactType` is knock's own `ArtifactType` enum
  (`image | helmChart | generic`, gaining `skill`). It is what an operator writes in YAML.
- **OCI vocabulary.** The manifest's `artifactType` field is a media type string —
  `application/vnd.knock.skill.v1` for a skill artifact.

**The scan worker reads the manifest, so it dispatches on the media type, not on the enum.** The
mapping enum → media type is one-way and lives with the stamp: policy `artifactType: skill` produces
manifest `artifactType: application/vnd.knock.skill.v1`. `ImageInfo.artifact_type` carries the media
type verbatim.

### Contracts to freeze

| Contract | Value |
|---|---|
| Skill artifact media type | `application/vnd.knock.skill.v1` |
| Gating report | the referrer whose `scan.tool` annotation is `skillspector-static` |
| Dossier report | `scan.tool` = `skillspector-llm`; never consulted by the gate |

The manifest projection looks for exactly the gating tool name. Any other report — including the
dossier one — is inert with respect to inclusion.

## Error handling

`classify_failure(stage, exit_code, stderr)` in `domain/scan_queue.py` already routes
transient / permanent / dead-letter. The new modes declare themselves there; nothing is rewritten.

| Stage | Failure | Classification | Consequence |
|---|---|---|---|
| `get_blob` | network, auth | transient | retry, then DLQ |
| `get_blob` | blob gone (artifact deleted) | permanent, drop | same as the existing F5 case |
| materialise | disk full | transient | retry |
| SkillSpector | exceeds `INGEST_MAX_BYTES` | permanent | never judgeable, therefore never in the manifest |
| SkillSpector | crash, timeout | transient → DLQ | no referrer → fail closed |
| SkillSpector (LLM) | missing credential, quota | **non-blocking** | the static report is attached regardless |
| `attach` | SARIF not recognised by `SarifMapper` | permanent | DLQ **and alert** — this means an upstream format change |
| `manifest` | gating referrer absent | fail closed | excluded **and logged** |

**Two rules deserve emphasis.**

A model-provider outage must **never** block the chain. The dossier report is optional by
construction; if the credential expires or the quota trips, the static report still attaches and the
gate still works. Otherwise the supply chain acquires a hard dependency on a model vendor.

Fail-closed exclusion must be **loud**. An artifact dropped from the manifest for lack of a verdict
is the same silent-disappearance class the projection's drop guard exists to prevent. `knock manifest`
lists its exclusions with reasons, and the drop guard catches mass exclusion.

## Testing

In the order they should be written.

1. **SARIF contract.** ~~Does a real SkillSpector report pass `detect_format` and then
   `SarifMapper`?~~ **Verified on 2026-08-29 — see the section above.** Keep it as a regression test:
   commit a real SkillSpector report as a fixture and assert `detect_format` returns `sarif` and
   `summarize` yields the expected facts. It guards against an upstream format change.
2. **Dispatch.** An artifact whose manifest `artifactType` is the skill media type routes to
   SkillSpector, an image routes to regis. Scanner stubbed; typing **not** stubbed. Include the
   degenerate case: an artifact with **no** `artifactType` must route to regis, not crash.
3. **Gate.** `gate_breached` at threshold boundaries over constructed referrer annotations.
4. **Fail closed.** Artifact with no gating referrer: absent from the manifest **and** logged.
5. **LLM non-blocking.** The static report attaches even when the model call fails.
6. **Hostile QA.** A fixture skill carrying a known prompt injection produces a finding and never
   reaches the manifest. SkillSpector ships fixtures of this kind under `tests/fixtures/`.

**The 2am-Friday test:** a known-malicious skill is pushed, scanned, and never appears in the
manifest — end to end, with no human in the loop.

## Trajectory

SkillSpector sits in the worker today because the gate is needed now and regis cannot target a
non-image artifact yet. The preferred long-term shape is regis as the single judge for every
artifact class, orchestrating SkillSpector as one more analyzer with JSON Logic policy on top.

**The SARIF contract makes that swap invisible to knock.** When regis learns non-image targets, the
worker's skill branch changes from "invoke SkillSpector" to "invoke regis", and everything
downstream — attach, gc, the gate, the projection — is untouched. Nothing built here is thrown away.

## Verified — the SARIF contract holds (2026-08-29)

SkillSpector 2.11.0 was installed from source and run with `--no-llm --format sarif` over its own
fixtures; the reports were fed to knock's real `detect_format` and `SarifMapper`.

| Check | Result |
|---|---|
| `detect_format(raw)` | `'sarif'` — auto-detected, no `--format` override needed |
| `SarifMapper().recognizes(doc)` | `True` |
| `SarifMapper().summarize(raw)` | succeeds; `tool='skillspector'`, `tool_version='2.11.0'` |
| SARIF version emitted | 2.1.0, one `run`, results carry `ruleId` and `level` |
| `vuln.unknown` count | 0 — every result carries a `level`, so nothing falls into the unknown bucket |

Gate behaviour over SkillSpector's own fixtures, via the existing `gate_breached`:

| Fixture | `vuln` crit/high/med/low | `fail_on=high` | `fail_on=medium` |
|---|---|---|---|
| `safe_skill` | 0/0/0/0 | pass | pass |
| `mcp_clean_skill` | 0/0/2/0 | pass | **blocked** |
| `mcp_poisoned_tool` | 0/6/2/2 | **blocked** | **blocked** |
| `malicious_skill` | 0/2/4/0 | **blocked** | **blocked** |

**`fail_on=high` is therefore the default threshold**: it passes the clean fixtures and blocks both
malicious ones. This closes what was open question 3. No knock code was needed to obtain this — the
existing mapper and gate handle SkillSpector output as-is.

### The one real defect: findings land in `vuln.*`, not `policy.*`

SkillSpector emits no SARIF `kind`. ADR 0039 keys on `kind` — deliberately, "never on the tool name",
to stay analyzer-agnostic — so every SkillSpector finding is classified as a **vulnerability** rather
than a policy verdict.

This matters operationally, not just semantically: the pipeline publishes `vuln` facts to
Dependency-Track and blast-radius is computed from them. A prompt-injection finding would be counted
as a CVE in the one dashboard security looks at during an incident.

**Do not fix this by rewriting the report in the adapter.** knock would then publish something other
than what the tool said, which damages the provenance the product exists to provide. Two acceptable
paths:

1. **Upstream (correct).** `kind: "fail"` is the standard SARIF value for a failed evaluation. Ask
   SkillSpector to emit it. The evidence above is a concrete, reproducible case to bring.
2. **Locally, until then.** Exclude skill artifacts from the Dependency-Track publish leg. The gate
   works either way; only the downstream vulnerability metrics need protecting.

## Open questions

1. **Where does the LLM report get shown to the human reviewer?** It is attached as a referrer, but
   the reviewing surface is the still-open question from the intake work ("who reviews, and where").
2. **Does SkillSpector run acceptably in the scan worker image**, or does it need its own container
   the way buildkitd does? It is a Python tool (`requires-python >=3.12,<3.15`) with a Dockerfile in
   its repo but no published image on ghcr or PyPI as of 2026-08-29 — it installs from source. That
   affects deployment composition, not the design.
3. **Does the `kind` defect get fixed upstream or worked around?** See above.
