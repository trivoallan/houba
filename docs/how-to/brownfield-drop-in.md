---
title: "Drop knock into an existing Jenkins/skopeo/Harbor intake"
description: "Replace a skopeo→Harbor tag-only mirror with knock: same per-image policy, digest-pinned provenance, a declared owner label, and a signed SBOM inventory — so incident-time blast radius is one query."
sidebar_position: 11
---

# Drop knock into an existing Jenkins/skopeo/Harbor intake

:::note
This page is the decision view: the two queries and the honest pitch. The step-by-step `make demo-mongobleed` walkthrough is in the [**Run it**](#run-it) section below.
:::

This is for a platform team that already mirrors external images (skopeo → Harbor) from a per-image YAML, adds OCI labels, and queries them in PowerBI/Datadog. knock replaces the intake step: same per-image policy, but every placed image now carries **digest-pinned** provenance, a first-class **owner** label, and a package **SBOM** — so the incident-time question becomes one query.

## The two queries, side by side

When a CVE drops you ask one question — *"which images ship the vulnerable package, and who owns them?"* — against two different datasets.

**Before (tag-only mirror, owner guessed from the repo path):**

```sql
-- joins on a mutable tag; owner is inferred from the project name, not a fact
SELECT image_tag, project_name AS guessed_owner
FROM mirror_inventory
WHERE image_tag LIKE '%mongo%';     -- which mongo? which digest? whose?
```

**After (knock: digest-pinned stamp + owner label + SBOM inventory):**

```sql
-- joins on the immutable digest; owner is read from io.knock.owners; package from the SBOM
SELECT digest, owners, package, version
FROM knock_inventory                -- one row per (digest, package) from the signed SBOM
WHERE package = 'mongodb-org-server'
  AND semver_in_range(version, '8.0.0', '8.0.16');  -- pseudo; adapt to your warehouse's version comparison
```

The diff between these two queries is the pitch.

## Ownership: declared once, digest-bound

knock does **not** discover owners. `io.knock.owners` is a value you declare **once**, in the policy, at the front door — and knock binds it to the image's immutable **digest**. The value of that is not "knock found the owner"; it is "the owner is declared once and is unambiguous forever, even when the same image shows up under two repos or a re-pointed tag." Pitch the capability honestly: *declared once, digest-bound, queryable* — a governance/discipline win, not magic identification. (The failure it prevents: the same image under two project paths yields two conflicting *guessed* owners "before"; one authoritative digest-pinned owner "after".)

## Why the SBOM inventory beats your scanner

Run your scanner against the official MongoDB image at a mongobleed-affected version:

```console
$ grype mongo:8.0.16 -o table | grep CVE-2025-14847   # (nothing)
$ trivy image --quiet mongo:8.0.16 | grep CVE-2025-14847   # (nothing)
```

Both report clean. But `mongodb-org-server` ships from MongoDB's **own apt repo**, not a distro feed — so neither scanner's CVE matcher fires on it. knock's syft SBOM **catalogs the package**, so the inventory query above finds the blast radius your scanners missed. That gap is exactly why a signed package **inventory** — not a scanner verdict — is what you query at incident time.

To find and fetch the SBOM behind that inventory query, see [Inspect an image's SBOM](./inspect-sbom.md).

## Run it

```bash
make demo-mongobleed
```

This seeds two mongobleed-affected MongoDB images (with a re-pointed `8.0` tag), runs them through knock's copy-path intake (`knock reconcile docs/examples/brownfield`), then:

- **Act 1** — the inventory query (`scripts/demo-mongobleed.sh`): the SBOM finds `mongodb-org-server` in the affected range with its owner and digest, while `grype` and `trivy` both report the image clean.
- **Act 2** — the gate (`scripts/demo-gate.sh`): `knock attach --fail-on high` blocks the vulnerable XZ image at intake (exit 1).

The maintainability win lives here too: the intake is now a declarative `MirrorPolicy` (`docs/examples/brownfield/mongo.yml`) under the knock core (≥90% tested, `mypy --strict`) — not a monolithic untested Groovy library.
