---
title: "Migrate off registry replication"
description: "Replace a legacy CI + registry-replication intake with knock destinations: same jobs, better provenance, and OCI referrers that survive to every team copy."
sidebar_position: 9
---

knock covers every job a legacy intake does. It is **not** a replication mechanism —
and that is the point.

## Why the replacement is safe

A typical legacy intake has three jobs:

| Legacy intake job | knock | What changes |
|---|---|---|
| Pull external image through a controlled gate | `knock reconcile` (copy or derive-and-stamp) | + provenance stamp + package SBOM |
| Harden (internal CAs, package mirrors) | declarative `transform` steps (`injectCA` / `rewritePackageSources`) | config, not pipeline code |
| Fan out to per-team registry projects | a policy `destinations` list (knock places + stamps + SBOMs each) | **OCI referrers survive** (replication strips them) |

Parity is on **jobs**, not on the mechanism.

## The load-bearing difference: referrers survive placement, not replication

Registry replication — Harbor ≤ 2.15.x is the canonical example — does a byte-for-byte
copy of the image manifest and layers. It does **not** carry OCI 1.1 referrers. That means
the SBOM (SPDX / CycloneDX, attached by knock at ingestion) and the cosign signature are
stripped in transit. A CVE query run against a replicated team copy is then **blind**: the
package inventory never arrived.

knock does not replicate. It **places** — it places and stamps directly into each destination
listed in the policy. The SBOM and signature are attached at placement time, into every
destination. Every team copy is self-describing from the moment it lands.

## Fan-out as config: the `destinations` list

Replace a replication rule with a `destinations` list in the import's policy. The example
below fans a single upstream image into two per-team registry projects:

```yaml
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source: { registry: docker.io, repository: library/redis }
  defaults:
    destinations:
      - { registry: internal, project: team-a, repository: redis }
      - { registry: internal, project: team-b, repository: redis }
  imports:
    - name: stable
      tags: { semverOnly: true }
```

Running `knock reconcile` against this policy places the selected tags into both
destinations, stamps each with the same provenance annotations, and attaches a
package-level SBOM to each placed digest. There is no separate replication job to wire.

## Migration checklist

1. **Audit your intake** — list every image and every destination team registry.
   Map each to an import in a `MirrorPolicy`.
2. **Translate hardening** — convert CA injection / package mirror patches from
   pipeline scripts to `transform` steps (`injectCA`, `rewritePackageSources`) in
   `spec.defaults.transforms`.
3. **Declare destinations** — one entry per team registry under `spec.defaults.destinations`
   (or per-import if different imports fan out differently).
4. **Dry-run first** — `knock reconcile --dry-run` reports what would be placed without
   touching the registry.
5. **Cut over** — once knock is placing correctly, decommission the replication rules.
   Keep the legacy intake paused (not removed) until the first CVE query confirms
   referrers are present in the team copies.

## What you do not need to change

- Downstream image references: knock places images at the same registry paths as
  before — no pipeline or Kubernetes manifest changes needed.
- Scanner configuration: scanners discover SBOMs as OCI referrers automatically;
  the SBOM consumer (Dependency-Track, Grype, etc.) follows the referrer link from
  the digest it already queries.
