---
title: "Architecture at a glance"
description: "The hexagon, the two placement paths, and why the stamp + SBOM is the product — a narrative overview of how knock is built."
sidebar_position: 1
---

knock is the single front door for external images: it places them (copy or rebuild-and-harden), stamps portable provenance, and attaches a package-level SBOM. This page tells the story of how it's built; for the full rationale and the C4 model, see the deep-dive links at the foot.

## The landscape

A platform or security engineer owns the policy and registry roster; app teams declare `MirrorPolicy` files and pull hardened images from the destination registry. knock talks to source registries (Docker Hub, Quay, GHCR…), a BuildKit daemon for rebuilds, a signer or KMS for attestations, an upstream scanner that hands knock its scan reports, and a usage oracle that `purge` consults before deleting anything.

```mermaid
flowchart LR
    eng([Platform / Security<br/>Engineer]):::person
    team([App / Product<br/>Team]):::person
    src[(Source registries<br/>Docker Hub · Quay · GHCR)]:::ext
    knock{{knock<br/>single front door · stamper}}:::core
    dst[(Destination registries<br/>Harbor · Zot · …)]:::ext
    bk[BuildKit]:::ext
    signer[Signer / KMS · Fulcio]:::ext
    scanner[Upstream scanner]:::ext
    oracle[Usage oracle / observability]:::ext

    eng -->|policy + roster| knock
    team -->|MirrorPolicy files| knock
    src -->|pull| knock
    knock -->|rebuild / harden| bk
    knock -->|sign attestations| signer
    scanner -->|scan reports| knock
    knock -->|place + stamp + SBOM| dst
    team -->|pull hardened images| dst
    knock -->|usage query at purge| oracle

    classDef person fill:#52606d,stroke:#39434c,color:#fff;
    classDef core fill:#1f6feb,stroke:#154da4,color:#fff;
    classDef ext fill:#eef1f5,stroke:#69707a,color:#1f2933;
```

## The hexagon

The layering is load-bearing: pure `domain/` holds all business logic with no I/O; `use_cases/` orchestrates it; `ports/` (typing Protocols) define the I/O contracts; `adapters/` (subprocess wrappers) implement those contracts; and a thin `cli/` layer parses arguments and wires everything together via `_di.py`. Dependency inversion means `use_cases/` and `adapters/` both point at `ports/`; nothing imports `adapters/` except the composition root — which is how you can test every use case with in-memory fakes.

```mermaid
flowchart TD
    cli["<b>cli/</b> — Typer, thin<br/>reconcile · purge · attach · audit · gc · verify · scan · version · render · _di (composition root)"]
    uc["<b>use_cases/</b><br/>loader · reconcile (orchestrator) · purge · attach · audit · gc · verify · scan_worker · report (RunReport)<br/><i>receive ports by keyword injection — never import adapters</i>"]
    domain["<b>domain/</b> — pure<br/>selection · reconcile · stamp · sbom · transforms/* · …"]
    ports["<b>ports/</b> — typing.Protocol<br/>RegistryPort · ImageBuilderPort · AttestorPort · SbomGeneratorPort · UsageOraclePort · Reporter · ClockPort"]
    adapters["<b>adapters/</b><br/>RegctlAdapter · BuildkitAdapter · CosignAdapter · SyftAdapter · CommandUsageAdapter · StructlogReporter · SystemClock"]

    cli --> uc
    uc --> domain
    uc -->|depends on| ports
    adapters -.->|implement| ports

    classDef pure fill:#b6e3d4,stroke:#04342c,color:#04342c;
    classDef port fill:#e9d8fd,stroke:#322659,color:#322659;
    classDef adapter fill:#fed7aa,stroke:#4a1b0c,color:#4a1b0c;
    class domain pure;
    class ports port;
    class adapters adapter;
```

## The two placement paths

knock selects the path from the policy itself: with no `transform` declared, the image is copied byte-for-byte via `regctl` and stamped; when a `transform` is present, knock rebuilds it through BuildKit (inject CA, rewrite package sources) and stamps the result. Both paths receive a syft SBOM attached as an OCI referrer; both can be signed with cosign.

A `MirrorPolicy` is the single declarative artifact: it names an upstream `source`, the tags to admit, and where to place them. The optional `transform` is the switch that flips an import from copy to rebuild:

```yaml
apiVersion: knock.io/v1alpha1
kind: MirrorPolicy
metadata:
  name: redis
spec:
  artifactType: image
  source:
    registry: docker.io
    repository: library/redis          # the external image to admit
  imports:
    - name: v7
      owners:
        - group:default/data-platform  # stamped as io.knock.owners
      tags:
        includeRegex: "^7\.2\."         # which upstream tags to admit
      # transform:                       # ← omit → copy path; declare → rebuild path
      #   - injectCA: { certs: [corp] }
      #   - rewritePackageSources: { mirror: corp }
      destinations:
        - project: mirror
          repository: redis
```

For the full schema see the [MirrorPolicy reference](../reference/schemas/mirror-policy.md); for runnable examples, the [example policy catalog](/examples).

```mermaid
flowchart TD
    pol[MirrorPolicy] --> dec{transform<br/>declared?}
    dec -->|no| copy[Copy path<br/>regctl copy]
    dec -->|yes| rebuild[Rebuild path<br/>buildctl: inject CA,<br/>rewrite pkg sources]
    copy --> stamp[Stamp provenance]
    rebuild --> stamp
    stamp --> sbom[syft SBOM<br/>attached as OCI referrer]
    sbom --> sign{signer<br/>configured?}
    sign -->|yes| signed[+ signed in-toto<br/>attestations]
    sign -->|no| place[Placed image<br/>in destination]
    signed --> place

    classDef path fill:#b6e3d4,stroke:#04342c,color:#04342c;
    class copy,rebuild path;
```

To walk the rebuild path step by step, see [Rebuild & harden an image](../how-to/rebuild-and-harden.md).

## Why the label is the product

The value lands at incident time, not placement time. The provenance stamp carries lineage and owners; the SBOM carries the package inventory; both ride the image digest as OCI referrers so they travel with every copy. When a CVE drops, "which images ship package P, and who owns them?" becomes one query in the org's own observability stack. knock produces the facts — it never runs the query itself.

```mermaid
flowchart LR
    img[Placed image<br/>@digest]
    img --- stamp[Provenance stamp<br/>lineage · owners · transform]
    img --- sbomref[SBOM referrer<br/>package inventory]
    cve[(CVE drops)] -.-> q{{Blast-radius query<br/>in your observability stack}}
    sbomref -->|which images ship pkg P?| q
    stamp -->|who owns them?| q
    q --> answer[Owner + affected image list<br/>in one query]

    classDef knock fill:#1f6feb,stroke:#154da4,color:#fff;
    class stamp,sbomref knock;
```

## Go deeper

- [Architecture & design](https://github.com/trivoallan/knock/blob/main/docs/architecture/design.md) — full rationale, design decisions, and trade-offs
- [The full C4 model (Structurizr views)](https://github.com/trivoallan/knock/tree/main/docs/architecture) — System Landscape, Context, Container, Hexagon, Component, and Deployment views
- [Decision records](https://github.com/trivoallan/knock/tree/main/docs/architecture/decisions) — ADRs linked to specs

For in-depth coverage of specific subsystems, see [transforms and signed attestations](attestations.md), [SBOM generation and attachment](sbom.md), and [deletion and retention policy](deletion-and-retention.md).
