---
title: "Verify a published stamp"
description: "Read the provenance stamp, fetch the package SBOM, and verify the signature on an image you did not build — with stock tools, no account and no cluster."
sidebar_position: 2
---

# Verify a published stamp

knock's stamp is meant to be read by people who did not build the image and have no reason to trust
whoever did. This guide verifies one you had no hand in producing.

The images live in **[knock-examples](https://github.com/trivoallan/knock-examples)**, where a weekly
workflow runs the example policies from this repository — checked out at a pinned tag, never copied —
and publishes the results. You are therefore verifying the exact policy file this site documents.

You need [`regctl`](https://github.com/regclient/regclient) and
[`cosign` **v3**](https://github.com/sigstore/cosign). No account, no credentials, no cluster.

## Read the stamp

```bash
regctl manifest get ghcr.io/trivoallan/demo/debian:bookworm-slim-eu \
  --format '{{json .Annotations}}' | jq .
```

```json
{
  "io.knock.owners": "group:default/platform,group:default/base-images",
  "io.knock.policy": "debian-tz",
  "io.knock.transform.steps": "setTimezone",
  "io.knock.variant": "eu",
  "org.opencontainers.image.base.digest": "sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171",
  "org.opencontainers.image.base.name": "docker.io/library/debian:bookworm-slim",
  "org.opencontainers.image.vendor": "Example Platform Team"
}
```

`org.opencontainers.image.base.*` are OCI-standard keys, so any scanner reads them without knowing
what knock is. `io.knock.transform.steps` is the knock-specific part: this image was **rebuilt**, not
copied, and the lineage says exactly what changed on the way through.

## Fetch the package SBOM

```bash
regctl artifact list ghcr.io/trivoallan/demo/debian:bookworm-slim-eu
```

Both SPDX and CycloneDX are attached as OCI referrers on the same digest — the package inventory that
turns *"which images ship the vulnerable package?"* into one query at CVE time. See
[Inspect an image's SBOM](inspect-sbom.md) to pull the document itself.

:::note GHCR serves no referrers API

GHCR does not implement the referrers API, so these are stored under the OCI specification's
**fallback tag schema**: each referrer gets a `sha256-<digest>` tag on the subject repository.
`regctl` resolves that transparently, which is why the command above works — but a raw `curl` against
`/v2/.../referrers/` finds nothing, and you will see `sha256-…` tags beside the real ones in a tag
listing. That is the fallback doing its job, not damage.

:::

## Verify the signature

```bash
cosign verify-attestation \
  --type https://knock.dev/predicate/transform/v1 \
  --certificate-identity-regexp '^https://github.com/trivoallan/knock-examples/\.github/workflows/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/trivoallan/demo/debian:bookworm-slim-eu
```

Note what you are trusting. The signing identity is not a key someone handed you — it is the URL of
the GitHub Actions workflow that produced the image, recorded in Sigstore's transparency log at
signing time. You verify *that this workflow ran*, which is checkable without trusting the publisher.

`--type` is required. Without it cosign looks for the default `custom` predicate, finds none, and
reports the predicates it *did* find — which reads like a missing signature but is the opposite. The
SBOMs are signed too; pass `https://spdx.dev/Document` or `https://cyclonedx.org/bom` to verify those.

## Where each fact actually lives

The three tiers of evidence sit in three different places, which matters as soon as you write your own
tooling against them:

| Fact | Where it lives | How to read it |
|---|---|---|
| The provenance stamp | annotations on the image manifest or index | `regctl manifest get` |
| SBOM, and knock's signed attestation | OCI **referrers** on the image digest | `regctl artifact list`, `cosign verify-attestation` |
| BuildKit's build provenance (rebuild path only) | an **attestation manifest inside the image index**, marked `vnd.docker.reference.type: attestation-manifest` | `regctl manifest get` on the index, then follow the descriptor |

The third row is the one that surprises people: BuildKit's `slsa.dev/provenance/v1` is *not* a
referrer, so a referrer probe will never find it no matter how it is filtered. It is a sibling
manifest in the index, linked back to the platform manifest by `vnd.docker.reference.digest`.

This also rules out a tool that gets suggested often:
[slsa-verifier](https://github.com/slsa-framework/slsa-verifier) verifies provenance from the
`slsa-github-generator` and Google Cloud Build only, and is no longer maintained. It reads neither
BuildKit's attestation nor knock's own predicate. `cosign` is the tool for both.

## The counter-example

`ghcr.io/trivoallan/bypass/busybox:1.37.0` was pushed **directly** to the same registry, never through
knock. Run the same three commands against it: no stamp, no SBOM, no signature.

That contrast is the argument for a mandated front door. A stamp on part of the fleet leaves a
blast-radius query with blind spots, and what never came through the door is ungovernable — see
[Audit coverage](audit-coverage.md) for finding those gaps against a registry that serves the catalog
API.
