---
title: "SARIF ingestion profile"
description: "The SARIF 2.1.0 contract any analyzer writes against so knock classifies its results correctly — vulnerability findings into vuln.*, governance verdicts into policy.*, keyed on result.kind."
sidebar_position: 4
---

# SARIF ingestion profile

The contract **any analyzer writes against** so knock ingests its report correctly. knock accepts
**SARIF 2.1.0** as the sole scan-report format (`knock attach … --report <file>`) and summarizes each
result into a tool-agnostic fact space attached to the image digest. knock keys on standard SARIF
fields only — **never on the producing tool** — so any analyzer that follows this profile is
supported without a knock change.

## What knock reads

From the report:

- `runs[].tool.driver.name` / `.version` → the `scan.tool` / `scan.tool.version` envelope facts.
- `runs[].tool.driver.rules[].properties.security-severity` → a rule-level score, used as a fallback
  for results that reference that rule by `ruleId`.
- `runs[].results[]` → each result is classified into exactly one fact key (below).
- `runs[].properties.ruleset_hash` → the `scan.ruleset.hash` fact, falling back to the first
  `runs[].results[].properties.ruleset_hash` (see [Ruleset fingerprint](#ruleset-fingerprint)).

The raw report travels verbatim as the OCI referrer blob; knock never rewrites it.

## Ruleset fingerprint

A tool version pins the *engine*, not the *rules*. For a vulnerability scanner the two coincide
closely enough; for a policy-as-code analyzer they do not — the same version with a different
playbook returns a different verdict. Without the ruleset recorded, a signed attestation says "this
digest passed" but not **under which rules**, so its meaning silently changes when the rules do.

A producer therefore declares `ruleset_hash`: an opaque, tamper-evident fingerprint of the
**resolved, enforced** ruleset (thresholds included). knock reads it from, in order:

1. `runs[].properties.ruleset_hash` — **the canonical home.** A run-level property bag is present on
   every run, breached or clean.
2. `runs[].results[].properties.ruleset_hash` — the first result carrying one. This accommodates
   producers that hang the fingerprint off a `kind: "pass"` receipt.

The value is carried **verbatim** as the `scan.ruleset.hash` fact — knock does not parse, normalize,
or validate it, and treats it as an opaque equality token. Only the first fingerprint found across
all runs is recorded.

:::warning Emit it at run level
Attaching the fingerprint **only** to a pass receipt loses it on exactly the runs where it matters
most: a report with breaches carries no receipt, so the failing verdict becomes the one that does
not say which rules produced it. Emit `ruleset_hash` in `runs[].properties` on **every** run.
:::

When no producer declares a fingerprint, **the fact is omitted entirely** — knock never derives a
stand-in from the tool version, the report digest, or the rule list. This is the rule ADR
[0020](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0020-revision-semantics.md)
set for `org.opencontainers.image.revision`: propagate what the source declares, or say nothing.
A consumer must treat an absent `scan.ruleset.hash` as *unknown*, never as *unchanged*.

## Finding classes — `kind` is the discriminator

knock splits results into two classes by the standard SARIF `result.kind`:

| Result has…       | Class                    | Fact space |
| ----------------- | ------------------------ | ---------- |
| **no** `kind`     | vulnerability **finding** | `vuln.*`   |
| an explicit `kind` | governance **verdict**   | `policy.*` |

An explicit `kind` **wins over** any CVSS `security-severity` the result carries: a result that
declares a `kind` is a verdict, even when it is also scored. This is how a policy / posture analyzer
(license, EOL, best-practice, compliance) keeps its verdicts out of the vulnerability counts.

## Classification rules

For each result:

1. **`kind` present** (a governance verdict):
   - `kind: "pass"` → `policy.passed`.
   - any other `kind` (`"fail"`, …) → `policy.<severity>` — severity from the result's
     `security-severity`, else its rule's, else its `level`.
2. **`kind` absent** (a vulnerability finding):
   - → `vuln.<severity>` — severity from `security-severity`, else its rule's, else its `level`.

Severity from a CVSS `security-severity` score:

| score   | bucket   |
| ------- | -------- |
| ≥ 9.0   | critical |
| ≥ 7.0   | high     |
| ≥ 4.0   | medium   |
| < 4.0   | low      |

Severity fallback from `level` when no `security-severity` is present:

| `level`           | bucket  |
| ----------------- | ------- |
| `error`           | high    |
| `warning`         | medium  |
| `note` / `none`   | low     |
| (other / absent)  | unknown |

:::note
`security-severity` is a string-encoded numeric CVSS score carried in `properties` (the GitHub
convention). Emit it on every scored result — knock's buckets are CVSS bands, finer than SARIF's
coarse `level`.
:::

## Published facts

Every attached scan referrer carries these annotation keys, each prefixed with the configured label
prefix (default `io.knock`) as `{prefix}.scan.<key>`. An empty prefix emits no summary annotations.

Envelope:

- `scan.tool`, `scan.format`, `scan.timestamp`, `scan.subject` (always present)
- `scan.tool.version` (when the report declares one)
- `scan.ruleset.hash` (when the report declares one — omitted otherwise, never fabricated)

SARIF facts (counts, string-encoded):

- `scan.vuln.critical`, `scan.vuln.high`, `scan.vuln.medium`, `scan.vuln.low`, `scan.vuln.unknown`
- `scan.policy.critical`, `scan.policy.high`, `scan.policy.medium`, `scan.policy.low`,
  `scan.policy.unknown`
- `scan.policy.passed`

The same facts populate the signed `https://knock.dev/predicate/scan/v1` attestation summary (see the
[scan-predicate schema](schemas/scan-predicate.md)).

## The `--fail-on` gate

`knock attach --fail-on <severity>` gates **only** on `vuln.*` counts (severity order
`critical > high > medium > low > unknown`). Governance verdicts (`policy.*`) are recorded in the
stamp but never affect the exit code.

## Minimal example

```json
{
  "version": "2.1.0",
  "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
  "runs": [
    {
      "tool": { "driver": { "name": "example-analyzer", "version": "1.2.0" } },
      "properties": { "ruleset_hash": "sha256:9f2c…" },
      "results": [
        { "ruleId": "CVE-2024-0001", "properties": { "security-severity": "9.8" } },
        {
          "ruleId": "license/gpl-in-proprietary",
          "kind": "fail",
          "level": "error",
          "properties": { "security-severity": "9.0" }
        },
        { "ruleId": "eol/base-image", "kind": "pass", "level": "none" }
      ]
    }
  ]
}
```

Resulting facts: `vuln.critical=1` (the `kind`-less CVE), `policy.critical=1` (the scored verdict),
`policy.passed=1` (the satisfied check); every other count is `0`. Plus `ruleset.hash=sha256:9f2c…`,
naming the rules all three verdicts were reached under.

## Producer guidance

To emit **governance verdicts** (not vulnerabilities):

- set `result.kind` on every verdict — `"fail"` for a breach, `"pass"` for a satisfied check (with
  `level: "none"`, per SARIF 2.1.0);
- carry the verdict severity as a CVSS-scaled `properties.security-severity` so knock buckets it
  (`policy.critical` … `policy.low`);
- use only `fail` / `pass` — knock treats every non-`pass` kind as a failed verdict;
- emit `ruleset_hash` in `runs[].properties` on **every** run — a breached run needs it as much as a
  clean one, and a signed verdict that omits it does not say which rules it was reached under.

A **vulnerability scanner** needs no changes: omit `kind`, carry `security-severity`, and knock
buckets findings into `vuln.*`. Emitting `ruleset_hash` is still worthwhile where the finding set
depends on a pinned advisory database or an ignore-file, not on the tool version alone.

## See also

- [Attach a scan result](../how-to/attach-scan.md) — the task this profile feeds.
- [scan-predicate schema](schemas/scan-predicate.md) — the signed `scan/v1` attestation.
- ADR [0039 — SARIF `kind` discriminates a policy verdict from a vulnerability finding](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0039-sarif-kind-discriminates-policy-from-vuln.md),
  which supersedes ADR [0027](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0027-sarif-finding-type.md).
- ADR [0047 — A signed scan verdict records the ruleset it was reached under](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0047-scan-ruleset-fingerprint.md).
