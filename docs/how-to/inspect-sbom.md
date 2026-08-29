---
title: "Inspect an image's SBOM"
description: "Find and fetch the SBOM referrer attached to a placed image, and enable CycloneDX alongside SPDX."
sidebar_position: 5
---

Every image `knock reconcile` places carries a package-level SBOM attached as an OCI referrer (for the
what and why, see [Package-level SBOM](../explanation/sbom.md)). This guide finds and fetches it, and
enables CycloneDX alongside SPDX.

## Find the SBOM referrer

The SBOM is attached via the OCI referrers API, so any distribution-spec-compliant client discovers
it. Its `artifactType` is the SBOM media type — `application/spdx+json` (or
`application/vnd.cyclonedx+json`):

```bash
regctl image referrers registry.example.com/demo/busybox@sha256:<digest>
```

## Fetch the SBOM document

```bash
regctl manifest get registry.example.com/demo/busybox@sha256:<sbom-referrer-digest> \
  --format body | jq '.packages | length'
```

The referrer's annotations record the producing tool and version (`io.knock.sbom.tool` /
`io.knock.sbom.tool.version`), the format, and the subject digest.

## Emit CycloneDX as well as (or instead of) SPDX

SBOM generation is always-on; `KNOCK_SBOM_FORMATS` chooses the format(s) — a global, non-empty
operator setting. One `syft` scan produces one referrer per format:

```bash
KNOCK_SBOM_FORMATS='["spdx-json","cyclonedx-json"]' uv run knock reconcile docs/examples/reference
```

See the [configuration reference](../reference/configuration.md) for the variable.

## Verify the signed SBOM

When `KNOCK_ATTEST_SIGNER` is set, each SBOM is *also* attached as a signed in-toto attestation under
knock's identity. Verify it with stock cosign — the predicate type is the canonical document type
(`spdxjson` / `cyclonedx`):

```bash
cosign verify-attestation --type spdxjson \
  --certificate-identity-regexp '.*' --certificate-oidc-issuer-regexp '.*' \
  registry.example.com/demo/busybox@sha256:<digest>
```

Tune the `--certificate-*` flags to your trust root; for `kms`/`key` signing use `--key` instead. The
raw referrer (above) is always present; this signed attestation is the trust tier — an admission
controller can *require* it. See [signed attestations](../explanation/attestations.md).
