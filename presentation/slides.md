---
theme: default
title: knock — the single front door for external images
---

# knock
### The single front door for external images

Stamper, not a mirror — provenance + SBOM on every placed image.

---

## The problem

- External images enter the org through many uncontrolled paths.
- When a CVE drops: *which images ship the vulnerable package, and who owns them?*
- Today that's a fire drill. knock makes it one query.

---

## The landscape

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

---

## Hexagonal by design

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

Pure `domain/`; `ports/` are the Protocol seams; `adapters/` are subprocess wrappers; nothing imports `adapters/` except the `_di` composition root.

---

## Two placement paths

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

No `transform` → copy + stamp. A `transform` → rebuild through BuildKit (inject CA, rewrite package sources) + stamp. Both get a syft SBOM; both can be signed.

---

## The label is the product

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

The stamp carries lineage + owners; the SBOM carries the package inventory. knock produces the facts; the org's observability stack runs the query.

---

## Coverage gates value

- A stamp on 40% of the fleet = a blast-radius query with blind spots.
- knock's value is proportional to being the *mandatory* front door.
- Enforcement levers: `attach --fail-on`, `audit --signed` / `--fail-on-unsigned`.

---

## Try it

- `knock reconcile <policy>` — place + stamp + SBOM
- Docs: https://trivoallan.github.io/knock/
- Architecture deep-dive: `docs/architecture/design.md`
