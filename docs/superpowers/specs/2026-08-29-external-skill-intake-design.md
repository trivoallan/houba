# Spec: external skill intake — git source, the `skill` artifact class, and a pinned marketplace manifest

Status: **Draft**
Date: 2026-08-29
Relates to: [0001](../../architecture/decisions/0001-mirror-policy-format.md) (MirrorPolicy, standard
keys for standard facts), [0011](../../architecture/decisions/0011-source-registry-credentials.md)
(source credentials), [0020](../../architecture/decisions/0020-revision-semantics.md) (revision is
propagated, never fabricated), [0021](../../architecture/decisions/0021-attach-fail-on-gate.md)
(the front door can say no), [0042](../../architecture/decisions/0042-platform-scan-pipeline-incremental-reconcile-fed.md)
(scan pipeline)
ADR: 0047 (proposed) — non-registry sources and the `skill` artifact class. **0046 is taken**
(`0046-showcase-examples-repository.md`).
Companion spec: [skill artifact scanning](2026-08-29-skill-artifact-scanning-design.md) — how the
content verdict is produced. This spec covers how the artifact gets in and how it reaches a laptop.

## Summary

Agent skills are markdown that steers an agent plus scripts that execute on a developer's machine.
Two attack surfaces at once, on hosts holding SI credentials. **Internal skills are not the problem**
— they already live in private repos and are distributed correctly. **External skills are**: today
nothing stops one from being installed from anywhere.

knock is already "the single front door for the external container images your organization runs".
An external skill is the same shape one artifact class over: an untrusted upstream thing that must
be ingested, reviewed, stamped, and republished internally before anyone runs it. ADR 0001 already
says knock is a front door for external OCI **artifacts** — this spec is the first time that word is
exercised.

So this is not a new product and not a new repo. It is a new source kind, a new artifact class, and
one genuinely new output: a `marketplace.json` manifest, because Claude Code cannot read OCI.

## Context: why not the obvious alternatives

- **A separate `oci-skills` repo** would open a second front door, contradicting the mandate knock
  sells. Rejected.
- **A plain git marketplace** (private repo + `strictKnownMarketplaces`) delivers the same human
  review in days rather than weeks and was recommended twice, by two independent reviewers. It was
  rejected deliberately: git tags are mutable, so it yields no immutable digest and no verifiable
  signature. **The objective is verifiable provenance, not review alone.** That is an owner decision
  with internal context, recorded so it is not re-litigated.
- **Content judgement inside knock** is out of scope by thesis: knock is a stamper, not a query
  engine. The verdict comes from the companion spec's scanner and, later, from regis.

## Non-goals

- Judging skill content. knock produces facts; the scanner produces the verdict.
- Freshness policy ("older than N days is red"). knock stamps the upstream revision and the ingest
  time; regis owns the rule.
- Detecting or blocking a skill copied into `~/.claude/skills` by hand. `strictKnownMarketplaces`
  blocks marketplace addition, not filesystem writes. **This is the one accepted hole**; closing it
  means shipping a verifier to every workstation, which is disproportionate at the current fleet
  size. Revisit when the fleet grows.

## Verified — the client contract (2026-08-29)

Everything below was tested against Claude Code 2.1.251 and real registries, not read from docs.

| Question | Answer |
|---|---|
| `zip -X` + fixed mtime → same digest twice? | **Yes.** `TZ` must be fixed too, or two machines in different zones diverge |
| OCI blob digest == the zip's sha256? | **Yes**, confirmed on two registries |
| Does pushing an arbitrary zip need `oras`? | **No.** `regctl artifact put --file-media-type application/zip` is enough, and regctl already ships in the runtime image |
| `archive` source over `http://` | **Rejected at manifest validation**, not at download |
| `archive` source at `https://localhost` | **Rejected at validation.** Binary string: `Archive URLs must use https:// and must not point at a loopback, link-local, or cloud-metadata host` |
| Cross-origin 307 redirect | **Followed.** Binary string: `a server-chosen cross-origin redirect must use https:// and must not point at a loopback, link-local, or cloud-metadata host`. Harbor's redirect to its storage backend is fine |
| `sha256` mismatch | `Plugin archive integrity check failed ... The archive was not installed.` Integrity is genuinely enforced |
| **`sha256` absent** | **No check at all.** The binary reads `if (t.sha256 && t.sha256.toLowerCase() !== w) throw ...` — a missing field silently skips verification |
| Registry blob, bare GET (ghcr, Harbor demo, **public** projects) | **HTTP 401** every time. An anonymous token is issuable but requires the challenge dance, which the `archive` source does not perform |
| Registry blob + `headers.Authorization` | **Download succeeds**, sha256 verified |
| Private-CA TLS without `NODE_EXTRA_CA_CERTS` | `unable to verify the first certificate` |
| Private-CA TLS with `NODE_EXTRA_CA_CERTS` | **`✔ Successfully installed plugin`** — full chain, end to end |
| `marketplace.json` served as an OCI blob | **Works.** `content-type: application/octet-stream` is accepted; `marketplace add` then `install` both succeed |
| `extraKnownMarketplaces.headers` on `marketplace add <url>` | **Not applied** → 401. The name is only known after the file is read |
| `extraKnownMarketplaces` in **user** settings at session start | **Does not auto-provision.** That is a managed-settings path |
| `sha256` on a marketplace source | **Silently ignored.** "Validating marketplace data" is schema `safeParse`, not integrity |

### The consequence that shapes the whole design

**The manifest is the trust root and nothing verifies it.** Archive digests are enforced, but those
digests come from the manifest. Whoever can write the manifest chooses what every workstation
installs — stamps, signatures and attestations included, because they supply the expected digests
themselves.

A registry blob URL is content-addressed, therefore immutable, therefore the only shape that gives
the manifest integrity. The cost is that any catalog change produces a new digest and a new URL.

**Decision: the manifest is published as an OCI blob and its digest-pinned URL is distributed through
managed settings.** Adding a skill to the catalog becomes a fleet operation. That is acceptable
because an incoming external skill is already a rare, reviewed, deliberate event.

## Architecture

```
   upstream (github, gitlab)                knock                          Harbor
            │                                 │                               │
       git repo ──▶ ports/Source ──▶ selection ──▶ packaging ──▶ stamp ──▶ blob + referrers
                    (generic, git is       (refs)   (deterministic  (revision  (signed)
                     the first non-                  zip, symlink    = commit
                     registry impl)                  refusal, size    sha)
                                                     gate)
                                                          │
                                              ┌───────────┴────────────┐
                                              ▼                        ▼
                                     scan verdict referrer      knock manifest
                                     (companion spec)           projects the registry,
                                              │                 includes an artifact ONLY IF
                                              └────────────────▶ its gating referrer passes
                                                                        │
                                                                        ▼
                                                        marketplace.json pushed as a blob
                                                                        │
                                                                        ▼
                                          managed settings: pinned URL + robot credential + internal CA
                                                                        │
                                                                        ▼
                                                              developer workstation
                                                     strictKnownMarketplaces = [internal only]
```

## Schema

`MirrorPolicy` already carries an `ArtifactType` enum (`mirror_policy.py:29`) with values
`image | helmChart | generic`, a field at line 189 with a validator, and `extra="forbid"` on the
model. `kind` is already taken: `Literal["MirrorPolicy"]` at line 231.

- **Add `skill` to the existing `ArtifactType` enum.** Additive; every existing policy stays valid
  unedited. Do **not** introduce a new `kind` field — that name is taken — and do not reuse `generic`,
  which would make policies unreadable on their own.
- **Make `source` polymorphic**: `registry` (today's shape, the default) or `git`. The discriminator
  must default to `registry` so existing policies parse unchanged. Confirm at implementation that
  this is additive under `extra="forbid"`; if it is not, the `apiVersion` must be bumped and a
  migration written. **Do not assume.**

Two vocabularies exist and must not be conflated: `MirrorPolicy.spec.artifactType` is knock's policy
enum, written by an operator; the OCI manifest's `artifactType` is a media type string,
`application/vnd.knock.skill.v1`. The mapping is one-way, enum → media type, and lives with the stamp.

## Components

The first draft of this table claimed reuse that does not survive contact with the source. An
independent reviewer demolished it and verification agreed on all eight points. This is the real state.

| Component | Nature | Notes |
|---|---|---|
| `domain/attestation.py`, `adapters/cosign_cli.py` | reuse as-is | |
| `use_cases/audit.py`, `domain/retention.py`, `domain/scan/*` | reuse as-is | |
| `domain/collision.py` | reuse as-is | already resolves name collisions |
| `ports/clock.py` | reuse as-is | **mandatory** for the stability window; do not bypass |
| `domain/semver.py` | reuse as-is | |
| `ports/source.py` | **new** | generic ingestion port; git is the first non-registry implementation. Written generic now: writing it git-specific costs a public-contract refactor later |
| `adapters/git_cli.py` | **new** | clone at ref, resolve to commit sha — the immutable upstream identity |
| `domain/packaging.py` | **new** | deterministic zip, symlink/path refusal, size gate |
| `adapters/marketplace_json.py` | **new** | consumer-format projection. **Adapter, never domain** — this is the first time knock knows about a named consumer, and the hexagon is what keeps that coupling confined |
| `ports/registry.py`: standalone artifact **write** | **new** | the port has `copy`, `annotate`, `put_referrer`, `inspect` — nothing pushes a standalone artifact from local content |
| `ports/registry.py`: blob **read** | **new** | symmetric gap; the companion spec needs it to materialise the zip for scanning |
| `ImageInfo.artifact_type` | extension | optional field with a default (there is precedent: `config_labels`), populated from the manifest's standard `artifactType`. The scan worker dispatches on it |
| `domain/stamp.py` | **extend** | `build_stamp_annotations` requires `source_registry/repository/tag/digest` and writes `base.name` / `base.digest`. A git repo has none of the four. Putting a git sha in `base.digest` is exactly the fabrication ADR 0020 forbids |
| `domain/coverage.py::is_stamped` | **extend** | it keys on `base.digest`, which a skill will not have. **Decide what "stamped" means for an artifact with no base image before writing the stamp**, or the coverage audit breaks |
| `domain/selection.py` | **extend** | 36 lines of regex + semver. The 7-day stability window is *not* here: it lives in `use_cases/reconcile.py:89` and reads `org.opencontainers.image.created` from the source manifest. A git tag has no such field — **define the git equivalent explicitly** |
| `use_cases/reconcile.py` | **extend, carefully** | 1066 lines, `_do_import` indexed on `source[tag].digest` throughout. This is surgery in the hottest file, not an addition |
| `adapters/syft_cli.py` | **extend** | scans `registry:{ref}` (line 115). A non-image artifact needs `dir:` over the unpacked tree |

## Decisions

| # | Subject | Decision |
|---|---|---|
| 1A | how `marketplace.json` is produced | **Projection.** A dedicated command derives it from the registry on demand. No stored state, so no divergence is possible |
| 1B | zip determinism | Fixed mtimes, lexicographic entry order, normalised permissions, and a **constant `date_time`** so no ambient `TZ` can leak in. The digest is then a pure function of *(content, plan, zlib build, compression level)* — **not of content alone**: `ZIP_DEFLATED` output is only deterministic for a fixed zlib build, and the level is currently unpinned and cannot be pinned through `zipfile.ZipFile(compresslevel=…)` (that argument is ignored when writing a prebuilt `ZipInfo`; it needs `ZipInfo.compress_level`). Cross-machine byte-identity therefore also requires pinning the zlib the packaging step runs against, e.g. one controlled container image |
| 2A | archive size gate | Refuse at packaging above a configurable threshold. **Bound it at ≤ 100 MiB**: the skill scanner caps ingestion there, and an artifact that cannot be scanned can never pass the gate. (The 256 MiB client cap quoted earlier could **not** be found in the binary — do not rely on it) |
| 2B | partial manifest projection | **Atomic + drop guard.** Nothing written if the registry walk fails; a manifest that loses entries versus the previous one requires explicit confirmation |
| 3A | path escape at unpack | **Refuse symlinks and non-strictly-relative paths at packaging.** This is packaging safety, not content judgement — it does not reopen the "knock doesn't judge content" line |
| 3B-bis | workstation → Harbor auth | **Read-only robot account** scoped to the skills project. Anonymous read was tried and does not work: Harbor challenges every bare GET, public projects included |
| 4A | revoking an installed skill | Documented runbook **plus** a `revoke` verb orchestrating registry removal, manifest regeneration, and the managed-settings fragment that disables the plugin fleet-wide |
| 5A-bis | declaring the artifact type | `skill` joins the existing `ArtifactType` enum. An earlier decision to add a `kind` field was wrong — `kind` is already taken |
| 6A | test fixtures for git ingestion | A **real local git repo** created by the fixture and cloned from a file path. Exercises the real git binary, needs no network. Mirrors what zot does for registries (ADR 0024) |
| 8A | freshness measurement | Delegated to regis. knock stamps the fact; regis owns the rule |
| 9A | reconcile pod egress | **Allowlist derived from the loaded policies.** A pod holding registry write credentials must not have open internet egress. Policies already declare their sources, so the list writes itself |
| 10A | consumer coupling | The manifest generator is an **adapter**. The domain only ever sees a list of stamped artifacts |
| T18 | where the manifest lives | Published as an OCI blob; its **digest-pinned URL distributed via managed settings**. The only shape that gives the trust root integrity |

## Fleet requirements

The managed-settings channel now carries **three** things. It is no longer a deployment convenience;
it is a structural dependency of the gate.

| Item | Why |
|---|---|
| `NODE_EXTRA_CA_CERTS` → internal CA | Without it the download fails on certificate verification |
| Harbor robot credential (`headers.Authorization`) | Without it every blob fetch is 401 |
| Digest-pinned manifest URL | The only integrity the trust root can have; changes on every catalog update |
| `strictKnownMarketplaces` = internal only | The lock that makes the front door mandatory |
| `disableCommandPluginSources: true` | Blocks the `command` source, which runs arbitrary commands at install |

**The whole design is declarative if this channel does not exist.** Confirm the fleet team can
deploy managed settings, and on what lead time, before starting implementation.

## Error handling

| Stage | Failure | Handling |
|---|---|---|
| `list_refs` | timeout, auth, repo missing | named errors; policy fails, run continues |
| `fetch` | ref vanished between list and fetch | skip that ref, warn |
| `fetch` | repo too large, unresolved LFS | named error, policy fails |
| packaging | no `SKILL.md` / invalid layout | named error — the client requires `.claude-plugin/` or one of `commands/`, `skills/`, `agents/`, `hooks/`, `themes/`, `output-styles/`, `monitors/`, `workflows/`, `SKILL.md`, `.mcp.json`, `.lsp.json` at the root, optionally inside a single wrapper directory |
| packaging | symlink or `../` entry | **refuse** (3A) |
| packaging | over the size threshold | **refuse** (2A) |
| push | registry auth, quota | existing `RegistryError` |
| projection | partial walk, or entry-count drop | **nothing written**, non-zero exit (2B) |
| projection | artifact missing its gating verdict | **excluded and logged** — a silent disappearance is the failure mode 2B exists to prevent |

Every failure is named. No catch-all. `reconcile` already reports per policy and exits with the worst
outcome — that stays.

## Testing

1. **Zip determinism** — zip the same tree twice, compare digests byte for byte. Without this test,
   decision 1B is an intention and regresses on the first refactor.
2. **Hostile QA** — a repo containing a symlink to `/etc/passwd`, an entry named `../../evil`, a file
   over the threshold, no tags, and no `SKILL.md`. **Five failures, five distinct named errors.**
3. **Atomic projection + drop guard** (2B), **size gate** (2A).
4. **Git fixtures** (6A) — real local repo; the real git binary, not a stub.
5. **Stamp for a base-less artifact** — asserts whatever `is_stamped` is redefined to mean.
6. **Stability-window equivalent for a git ref** — pinned clock via `ports/clock.py`.
7. **End-to-end** — push, project the manifest, install on a real client against a TLS registry with
   a private CA. Already proven manually on 2026-08-29; make it a regression test.

**The 2am-Friday test:** a skill whose upstream has not moved is re-reconciled and produces **no** new
digest, no manifest change, and no workstation re-download.

## Effort

Roughly **5 to 6 human weeks / 3 to 4 days with CC**. An earlier estimate of 3.5 to 4 weeks omitted
the stamp signature extension, the two missing port methods, and the surgery inside a 1066-line
`reconcile.py`.

## Open questions

1. **Who reviews, and where?** The automated gate has a home (the companion spec). The human review
   does not. A PR in a policy repo? The scanner's report? This blocks nothing in the code but leaves
   the gate half-defined.
2. **What does "stamped" mean for an artifact with no base image?** `coverage.py::is_stamped` depends
   on `base.digest`. Must be settled before the stamp is written.
3. **Can the fleet team deploy managed settings, and on what lead time?**
4. **What is the git equivalent of the 7-day stability window?**
5. **Is making `source` polymorphic genuinely additive** under `extra="forbid"` on a frozen
   `apiVersion`, or does it need a version bump?
6. **What does the auditor actually require — traceability or prevention?** Still unanswered, and it
   is the question that decides whether five weeks of work were necessary.
