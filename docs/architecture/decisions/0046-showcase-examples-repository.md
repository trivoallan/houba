# 46. Showcase examples repository — the stamp, publicly verifiable

Date: 2026-08-29

## Status

Accepted

## Context

The roadmap's *Now* is adoption: making the mandate demonstrable. The kind demo is complete and
honest, but it requires a clone, a build, and a cluster before anything is visible. Nothing knock
produces is publicly inspectable. A product whose value is a stamp should be provable by reading a
stamp, from a stranger's terminal, with no clone and no credentials.

## Decision

- A **separate public repository**, `knock-examples`, whose scheduled CI runs real `MirrorPolicy`
  files against real upstreams and publishes stamped, SBOM-carrying, keyless-signed images to GHCR.
  It is a **showcase**, not a test bed: where "stays green" and "detects regressions" conflict, green
  wins.
- **No policy is copied.** `docs/examples/` stays the single source of truth; the workflow checks it
  out at a pinned ref, so the visitor verifies the exact file published on the docs site. This buys
  the isolation of a separate repository without the drift a duplicated example set guarantees.
- **`RegistryConfig.host` must accept a path prefix**, with `regctl registry login` receiving only the
  host part. GHCR requires the owner as the first path segment; without this, `project: demo` cannot
  be replayed verbatim. A genuine portability gap — GitLab and Artifactory are path-namespaced too.
- **`verify.sh` is both the CI's acceptance step and the README's copied commands**, so the showcase
  cannot promise what it does not do. knock has no transaction: a SBOM or signature failure after the
  copy leaves a half-stamped image, which would refute the product publicly.
- The **canary publishes to a throwaway namespace** rather than running `--dry-run`. A dry-run canary
  exercises neither stamp, SBOM, nor signature, which is too little to justify a second workflow.
- **Coverage (`knock audit`) is deferred to its own specification.** `audit` / `gc` / `purge` enumerate
  through the OCI catalog API, which GHCR does not implement. Resolving it means a GitHub REST
  enumeration path or a self-hosted catalog-capable registry — neither should block the stamp proof.

## Consequences

- knock gains path-prefixed registry hosts, widening the set of registries it can target.
- A third way knock runs — a CI runner, no Kubernetes, no ArgoCD — so `workspace.dsl` gains a third
  `deploymentEnvironment` alongside greenfield and brownfield.
- The published showcase is a standing regression signal against the real world as a side effect,
  and the nightly canary warns before a knock bump breaks it.
- Two policies only (`reference/busybox`, `reference/debian-tz`): the ones running with no
  organization-specific configuration. `hardened/redis.yml` names an internal CA and mirror with no
  public counterpart; `reference/debian-xz` needs a seeded fixture.

Full spec: [docs/superpowers/specs/2026-08-29-showcase-examples-repo-design.md](../../superpowers/specs/2026-08-29-showcase-examples-repo-design.md)
