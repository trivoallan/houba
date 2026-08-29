---
title: "Gate a promotion or CI step with knock verify"
description: "Turn knock's signed scan attestation, provenance stamp, and SBOM referrer into a single exit-0/1 gate with knock verify."
sidebar_position: 4
---

`knock verify <ref>` is a **read-only gate**: it reads the facts that `attach` and `reconcile`
already placed on a digest (signed scan attestation, provenance stamp, SBOM referrer) and
produces a single pass/fail verdict. Exit 0 = every required fact passes. Exit 1 = at least one
does not. It never scans, never writes, and never mutates the registry.

:::note The division of labour
`attach` **writes** scan provenance (signs and publishes the attestation). `verify` **reads** it.
The gate lives at the point where a promotion or a CI step needs a binary answer; the scan itself
lives upstream where the scanner runs.
:::

## 1. What `verify` checks

`--require` selects which facts to evaluate. The default is `scan-pass` alone.

| `--require` value | Source | Trust |
|---|---|---|
| `scan-pass` | the **signed** in-toto scan attestation (`knock/predicate/scan/v1`) | **signature-verified** via cosign; freshness from the signed `attested_at` |
| `stamp` | manifest annotations | **presence** of `{KNOCK_LABEL_PREFIX}.artifact.type` |
| `sbom` | OCI referrers | **presence** of an SPDX or CycloneDX referrer |

Use `--require scan-pass,stamp,sbom` to require all three; any comma-separated subset is valid.

## 2. Gate a CI step on a signed scan

Run a scan, attach it with `knock attach`, then call `knock verify` to gate the next step:

```bash
# 1. Scan (run your scanner; example with Trivy)
trivy image --format sarif --output scan.sarif.json registry.example.com/lib/redis@sha256:abc…

# 2. Attach (sign and publish the attestation — needs KNOCK_ATTEST_SIGNER)
export KNOCK_ATTEST_SIGNER=keyless
knock attach registry.example.com/lib/redis@sha256:abc… --report scan.sarif.json

# 3. Gate (exit 0 = pass, 1 = fail)
knock verify registry.example.com/lib/redis@sha256:abc… \
  --require scan-pass \
  --max-severity high \
  --max-age 7d
```

The gate fails closed on anything missing or unverifiable: no attestation, an unverifiable
signature, a stale scan, or a severity breach all produce exit 1.

## 3. Wire it as a Kargo `AnalysisTemplate`

[Kargo](https://kargo.io) runs an `AnalysisTemplate` as a promotion gate. Add `knock verify` as
a job-based analysis to block a promotion when the image's scan is absent, stale, or above your
severity threshold:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: knock-scan-gate
spec:
  args:
    - name: image-ref     # the fully-qualified digest from the Kargo freight
  metrics:
    - name: scan-gate
      provider:
        job:
          spec:
            backoffLimit: 0
            template:
              spec:
                restartPolicy: Never
                containers:
                  - name: gate
                    image: ghcr.io/trivoallan/knock:latest
                    args:
                      - verify
                      - "{{args.image-ref}}"
                      - --require=scan-pass
                      - --max-severity=high
                      - --max-age=7d
                    env:
                      - name: KNOCK_ATTEST_SIGNER
                        value: keyless
                      - name: KNOCK_ATTEST_VERIFY_IDENTITY
                        valueFrom:
                          secretKeyRef:
                            name: knock-attest
                            key: verify-identity
                      - name: KNOCK_ATTEST_VERIFY_OIDC_ISSUER
                        valueFrom:
                          secretKeyRef:
                            name: knock-attest
                            key: verify-oidc-issuer
                      - name: KNOCK_REGISTRIES
                        valueFrom:
                          secretKeyRef:
                            name: knock-registries
                            key: registries
```

Pass `{{args.image-ref}}` as a pinned digest reference (`registry/image@sha256:…`) from the
Kargo freight so the gate is always digest-bound, never tag-based.

## 4. Keyless vs key/KMS verification

`knock verify` inherits the same signer config as `knock attach`, selecting the verification
mode from `KNOCK_ATTEST_SIGNER`.

### Keyless (Sigstore / Fulcio)

```bash
export KNOCK_ATTEST_SIGNER=keyless
export KNOCK_ATTEST_VERIFY_IDENTITY=https://github.com/your-org/.github/workflows/sign.yml@refs/heads/main
export KNOCK_ATTEST_VERIFY_OIDC_ISSUER=https://token.actions.githubusercontent.com

knock verify registry.example.com/lib/redis@sha256:abc… --require scan-pass
```

`KNOCK_ATTEST_VERIFY_IDENTITY` and `KNOCK_ATTEST_VERIFY_OIDC_ISSUER` scope the trust to the
specific workflow and issuer that signed the attestation. Without them, cosign applies its
default certificate-identity check.

### Key or KMS

```bash
export KNOCK_ATTEST_SIGNER=key
export KNOCK_ATTEST_KEY_REF=/etc/knock/cosign.pub   # or a KMS URI

knock verify registry.example.com/lib/redis@sha256:abc… --require scan-pass
```

In key mode, `KNOCK_ATTEST_KEY_REF` points at the public key (file path or KMS reference
understood by cosign). The transparency-log check is skipped automatically when using a key
(`--insecure-ignore-tlog` is set internally), so air-gapped setups work without a Rekor instance.

## 5. Roster-driven authentication

`knock verify` uses the same `KNOCK_REGISTRIES` roster as all other commands.

```bash
export KNOCK_REGISTRIES='{
  "prod": {
    "host": "registry.example.com",
    "username": "robot$knock",
    "password": "s3cr3t"
  }
}'

# host-match: knock finds the roster entry for registry.example.com automatically
knock verify registry.example.com/lib/redis@sha256:abc… --require scan-pass

# --registry override: force a specific roster entry
knock verify registry.example.com/lib/redis@sha256:abc… --require scan-pass --registry prod
```

With no `--registry` and no matching roster entry, `verify` falls back to ambient regctl config
(the same behaviour as `attach`).

## 6. Machine-readable output

`--output json` emits a structured object instead of the default human-readable lines, useful
for downstream parsing in CI pipelines:

```bash
knock verify registry.example.com/lib/redis@sha256:abc… \
  --require scan-pass,stamp,sbom \
  --output json
```

```json
{
  "passed": false,
  "outcomes": [
    {"requirement": "scan-pass", "passed": true, "detail": "severity <= high, attested 3600s ago"},
    {"requirement": "stamp",     "passed": true, "detail": "knock stamp present"},
    {"requirement": "sbom",      "passed": false, "detail": "no SBOM referrer"}
  ]
}
```

The exit code is always independent of `--output`: exit 0 when all requirements pass, exit 1
when any fails.

## 7. Exit-code contract

| Exit | Meaning |
|------|---------|
| `0`  | all required facts pass |
| `1`  | at least one required fact breached, or fail-closed (attestation missing, unverifiable, stale, or unparseable) |
| `2`  | operational failure (registry unreachable, cosign binary absent, auth error) |
| `3`  | invalid config (`--require` value unknown, `--max-age` unparseable, roster entry not found) |

Gate-1 is always a clean verdict, never an unexpected crash. The tool is fail-closed: a missing
or unverifiable attestation is a gate failure (exit 1), not an error (exit 2).

## 8. Limiting scope

`--require scan-pass` needs `KNOCK_ATTEST_SIGNER` configured; `--require stamp` and
`--require sbom` do not (they are presence checks, not signature verifications).

`--require stamp` also needs a non-empty `KNOCK_LABEL_PREFIX` (the default is `io.knock`).
With an empty prefix, knock emits only generic OCI annotation keys and there is no knock sentinel
key to check, so the requirement will always fail.
