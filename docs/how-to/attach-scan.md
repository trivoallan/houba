---
title: "Attach a scan result"
description: "Ingest an upstream SARIF report as a signed OCI referrer with knock attach, using --fail-on as a severity CI gate."
sidebar_position: 3
---

knock does not run scanners. A scan is produced **upstream** (your CI, a registry-native
scanner, or a scan service); knock ingests its report and stamps it as a portable OCI
**referrer** on the image's digest. This makes "which images carry a critical CVE?" one
referrers query at incident time.

:::note SBOM vs scan
Don't confuse this with the **SBOM** knock *generates* on every image it places — copy and rebuild alike: the SBOM is the package **inventory** (what's inside), produced by knock after placement; a scan is a **vulnerability assessment** (what's wrong with those packages), produced by a scanner knock does not run and merely ingests here. Building an inventory is not scanning.
:::

## 1. Produce a report upstream (example: Trivy emitting SARIF)

```bash
trivy image --format sarif --output scan.sarif.json harbor.corp/lib/redis:7.2.0
```

## 2. Attach it

```bash
knock attach harbor.corp/lib/redis:7.2.0 --report scan.sarif.json
# attached sarif scan (trivy 0.50.1) → harbor.corp/lib/redis@sha256:ref…
#   subject=sha256:abc…  vuln.critical=1 vuln.high=0 vuln.medium=1 vuln.low=0 vuln.unknown=0
```

The referrer manifest carries the summary annotations (`io.knock.scan.*`) and the raw
SARIF as its blob. Re-running `attach` after a fresh scan adds a new referrer (history).

[`sample.sarif.json`](https://github.com/trivoallan/knock/blob/main/docs/examples/scan/sample.sarif.json) is a runnable example report (1 critical, 1 medium).

## 3. Sign it (verifiable scan provenance)

With a signer configured, `knock attach` also emits a **signed** in-toto attestation
(`https://knock.dev/predicate/scan/v1`) over the image digest — additive to the raw report
referrer above. This is what lets an admission controller *require* a signed scan.

```bash
export KNOCK_ATTEST_SIGNER=keyless          # or kms | key
export KNOCK_ATTEST_BUILDER_ID=knock://ci   # identifies this knock attester
knock attach harbor.corp/lib/redis:7.2.0 --report scan.sarif.json
# attached sarif scan (trivy 0.50.1) → harbor.corp/lib/redis@sha256:ref…
#   subject=sha256:abc…  vuln.critical=1 …
#   signed: https://knock.dev/predicate/scan/v1 → sha256:att…
```

Off by default: with no `KNOCK_ATTEST_SIGNER`, only the unsigned referrer is attached.

## 4. Gate CI on severity (`--fail-on`)

`attach` is observational by default (exits 0). Pass `--fail-on <severity>` to turn it into a
CI gate: if the ingested scan has any finding **at or above** the threshold, attach exits 1.

Severity order (highest to lowest): `critical > high > medium > low > unknown`.

```bash
# Fail CI when any high or critical finding is present
knock attach registry.example.com/lib/redis:7.2.0 --report scan.sarif.json --fail-on high
# attached sarif scan (trivy 0.50.1) → registry.example.com/lib/redis@sha256:ref…
#   subject=sha256:abc…  vuln.critical=1 vuln.high=0 vuln.medium=1 vuln.low=0 vuln.unknown=0
# gating: scan has a finding at or above high (--fail-on)
# exit 1
```

The raw referrer is always attached first; the gate only controls the exit code. With no
findings at or above the threshold the command exits 0 (gate passes). Use `--fail-on low` or
`--fail-on unknown` to catch every finding including unknowns.

## 5. Roster-driven authentication

`knock attach` authenticates against the `KNOCK_REGISTRIES` roster — the same roster that
`reconcile` and `audit` use — so credentials are declared once and applied consistently.

### 5a. Host-match (default)

Set the roster and run `attach` as usual. knock parses the host from the image ref
(`harbor.corp` in the example below), finds the matching roster entry, and logs in before
touching the registry. No extra flag needed.

```bash
export KNOCK_REGISTRIES='{
  "prod": {
    "host": "harbor.corp",
    "username": "robot$knock",
    "password": "s3cr3t"
  }
}'

knock attach harbor.corp/lib/redis:7.2.0 --report scan.sarif.json
# knock matches harbor.corp → roster entry "prod", logs in, then attaches.
```

### 5b. `--registry` override

Use `--registry <name>` to force a specific roster entry regardless of the ref's host — useful
when the image is behind a pull-through proxy or the ref's host differs from the roster name:

```bash
knock attach harbor.corp/lib/redis:7.2.0 --report scan.sarif.json --registry prod
```

`--registry` with a name that is not in the roster exits 3 (`ConfigError`), consistent with the
rest of the CLI.

### 5c. Silent fallback

When no `--registry` is given and the ref's host is not in any roster entry (for example a
public image or an empty roster), `attach` configures nothing and falls back to ambient regctl
config — exactly today's behaviour. No flag and no roster entry required for public registries.

## Posture reports (governance verdicts, not vulnerabilities)

A SARIF report is not always a vulnerability scan. Policy / posture analyzers that emit SARIF
(license, EOL, best-practice, or compliance tools) report **evaluation outcomes** — each result
carries an explicit SARIF `kind` (`pass` / `fail` / …) marking a verdict rather than a finding.

`knock attach` keys on that standard SARIF signal: a result with an explicit `kind` is a **governance
verdict** and **wins over** any CVSS `security-severity` it carries, so it is summarized in the
`policy.*` space — never inflating the `vuln.*` counts:

```bash
uv run knock attach --format sarif posture.sarif.json harbor.corp/lib/redis:7.2.0
# attached sarif scan (posture-analyzer 1.x) → harbor.corp/lib/redis@sha256:ref…
```

The stamp then carries `io.knock.scan.policy.<severity>` (a failed verdict bucketed by its severity,
e.g. `policy.critical`) and `io.knock.scan.policy.passed`, alongside the `io.knock.scan.vuln.*`
buckets. knock keys on the SARIF `kind`, never on the tool name — so any analyzer emitting `kind`
gets governance bucketing for free. The `--fail-on <severity>` gate acts on `vuln.*` only — policy
verdicts are reported in the stamp, not gated.

When the report declares a `ruleset_hash`, the stamp also carries `io.knock.scan.ruleset.hash` — the
fingerprint of the rules the verdict was reached under, so a signed "passed" says *under which
policy*. See the [SARIF ingestion profile](../reference/sarif-ingestion-profile.md#ruleset-fingerprint).

### End-to-end: any SARIF analyzer at the front door

The same path works for **any analyzer that emits SARIF** — a vulnerability scanner, or a
policy-as-code engine that also covers **licenses, EOL, and best-practices** beyond CVEs. The
analyzer reads the placed, hardened front-door image itself; knock does not invoke it. You run your
analyzer, then publish its verdict as portable provenance:

```bash
# any SARIF-emitting analyzer: read the placed image, apply your policy
<analyzer> registry.example.com/lib/redis@sha256:abc… -o sarif > findings.sarif.json
# knock: bind the verdict to the digest as a signed referrer
knock attach registry.example.com/lib/redis@sha256:abc… --report findings.sarif.json
```

That verdict is now a signed referrer on the digest, read **by digest** by an admission controller
(Kyverno), Dependency-Track, or an audit — the same join key as the stamp and SBOM. The division of
labour: **the analyzer decides, knock carries, the admission controller enforces** (signed-scan +
max-age, [ADR 0032](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0032-attach-is-scan-provenance-not-a-store.md)). Read
at the **front door**: a registry that strips OCI referrers on replication (e.g. Harbor) won't carry
them downstream — follow the digest back to where knock attached them.

knock's only requirement is SARIF: it stays analyzer-agnostic (the `SarifMapper` handles
CVSS-scored vulnerabilities and rule `pass`/`fail` alike), so swapping or adding analyzers never
touches knock.
