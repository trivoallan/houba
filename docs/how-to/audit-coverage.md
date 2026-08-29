---
title: "Audit coverage"
description: "Find images that lack the stamp with knock audit, and gate CI on coverage, signing, or SBOM presence."
sidebar_position: 6
---

Once images are flowing through knock, **`knock audit`** answers the coverage-gate question:
*which images in the registry do NOT carry knock's provenance stamp?* It walks the configured
registries (or a single `--registry NAME`), reads each image's annotations, and reports each as
covered or uncovered — the blind-spot report that makes the front door verifiable.

```bash
# after a reconcile, against the local registry:2 from Getting started
uv run knock audit
# UNCOVERED localhost:5001/demo/other-image:latest
# audit  scanned=13 covered=12 uncovered=1 errored=0
```

It is **read-only** (never deletes or stamps) and **report-only by default** (exit 0). For a CI
gate, pass `--fail-on-uncovered` to exit non-zero when any image lacks the stamp:

```bash
uv run knock audit --fail-on-uncovered    # exit 1 if uncovered > 0
```

An image counts as covered when it carries the knock lineage annotation (`io.knock.policy`, or
the OCI `org.opencontainers.image.base.digest` when `KNOCK_LABEL_PREFIX` is empty). That
`base.digest` fallback only applies to image-derived artifacts; a source-derived artifact (e.g.
a git-ingested skill) has no base image, so it requires a non-empty `KNOCK_LABEL_PREFIX` to be
stamped at all. `KNOCK_LOG_FORMAT=json` emits the full structured `CoverageReport`.

## The trustworthiness tier

For the trustworthiness tier, add `--signed`: for each *stamped* image it also probes for a
signed attestation referrer (a present cosign bundle ⇒ signed; no pull-and-verify), distinguishing
*signed* from *merely stamped*:

```bash
uv run knock audit --signed
# UNSIGNED  localhost:5001/demo/legacy-image:latest
# audit  scanned=13 covered=12 uncovered=1 signed=11 unsigned=1 errored=0
```

As a CI gate, `--fail-on-unsigned` exits non-zero when any stamped image is unsigned (it implies
`--signed`):

```bash
uv run knock audit --fail-on-unsigned    # exit 1 if unsigned > 0
```

## The SBOM tier

For the SBOM tier, add `--sbom`: for each *stamped* image it also probes for a package SBOM
referrer (a present SPDX or CycloneDX referrer ⇒ with SBOM; no content verification), distinguishing
*with SBOM* from *merely stamped*:

```bash
uv run knock audit --sbom
# NO-SBOM  localhost:5001/demo/legacy-image:latest
# audit  scanned=13 covered=12 uncovered=1 with_sbom=11 without_sbom=1 errored=0
```

`--sbom` is observational only (no `--fail-on-no-sbom` gate exists). It can be combined with
`--signed` to check both tiers in a single pass:

```bash
uv run knock audit --signed --sbom
```
