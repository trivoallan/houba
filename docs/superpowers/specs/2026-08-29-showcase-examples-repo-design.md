# Showcase examples repository — design

*Status: designed, not implemented. Date: 2026-08-29.*

## Problem

The roadmap's *Now* is **adoption**: making the mandate demonstrable to a target organization. The
demo today runs on kind (`make local`, `make demo-mongobleed`) — it is complete and honest, but it
requires the visitor to clone, build, and run a cluster before seeing anything. Nothing knock produces
is publicly inspectable.

*The label is the product.* A product whose value is a stamp should be provable by reading a stamp,
from a stranger's terminal, in thirty seconds, with no clone and no credentials.

## Decision

A **separate public repository** — `knock-examples` — whose CI executes real `MirrorPolicy` files
against real upstreams and publishes stamped, SBOM-carrying, signed images to GHCR on a schedule.
The repository is a **showcase**, not a test bed: it exists to be looked at.

It contains **no copied policies**. The policies stay in `docs/examples/` in the knock repository —
the single source of truth, already schema-validated by `tests/unit/use_cases/test_examples_schema.py`
— and the workflow checks them out at a pinned ref. The visitor therefore verifies the exact file
published on the documentation site.

### Alternatives rejected

- **A standalone repository carrying its own copy of the policies.** Forkable as-is, fully isolated,
  but duplicates `docs/examples/`. Two sets of examples drift, which `CLAUDE.md` forbids outright
  ("Examples must never drift from the specs"). The isolation benefit is preserved by C at the cost
  of one `actions/checkout` step.
- **No new repository — a scheduled `showcase.yml` inside knock.** Zero duplication, but the showcase
  is not forkable, it drowns in a tool repository, and its red shows up in knock's own Actions tab.

## Non-goals

- **Not a test bed.** Regression detection against the real world is a welcome side effect, not the
  purpose; where the two conflict, the showcase staying green wins.
- **Not a fork-and-run template.** A GitHub-Actions-driven front door as a *second reference
  deployment* is a much larger commitment, distinct from this.
- **Not a coverage report.** See *Deferred* below.

## Architecture

Two repositories, one source of truth for policies.

```
trivoallan/knock                        (unchanged)
  docs/examples/reference/busybox/      ← copy path, self-contained
  docs/examples/reference/debian-tz/    ← rebuild path; setTimezone is the only built-in
                                          transform needing no organization config

trivoallan/knock-examples               (new, thin — carries no policy)
  README.md                             ← the landing page: the verification commands
  knock.env                             ← the single pin: KNOCK_VERSION=0.8.0
  verify.sh                             ← the acceptance test AND the README's own commands
  bypass/Dockerfile                     ← an image pushed outside knock
  .github/workflows/showcase.yml        ← publishes, from the pinned tag
  .github/workflows/canary.yml          ← replays main into a throwaway namespace
```

Only two example policies are in scope, chosen because they run with no organization-specific
configuration: `busybox` (copy path, semver aliases) and `debian-tz` (rebuild path, one source tag
fanned into two regional variants). `hardened/redis.yml` is excluded — it names an internal CA and an
internal package mirror that have no public counterpart. `reference/debian-xz` is excluded — it sources
a fixture seeded into the in-cluster registry, so it is not standalone.

### `showcase.yml` — weekly, plus `workflow_dispatch`

1. `actions/checkout` of this repository.
2. `actions/checkout` of `trivoallan/knock` at `${KNOCK_VERSION}`, into `knock/`.
3. Log in to GHCR with `GITHUB_TOKEN` (`packages: write`).
4. **Log in to Docker Hub** with a PAT held as a repository secret. Both policies source `docker.io`,
   and anonymous pulls from shared GitHub runner IPs are throttled aggressively; without this the
   showcase is red intermittently for a reason unrelated to knock.
5. `docker run ghcr.io/${OWNER}/knock:${KNOCK_VERSION} reconcile knock/docs/examples/reference/busybox`,
   then the same for `debian-tz`. (`reconcile` takes one directory; the parent directory also holds
   `debian-xz`, which is out of scope, so the two are invoked separately rather than recursively.)
6. `regctl image copy` of the bypass image.
7. `verify.sh` against what was just published (see *Failure modes*).

The `debian-tz` policy takes the **rebuild path**, so the runner needs a BuildKit daemon, not just the
`buildctl` the runtime image bundles: `moby/buildkit` runs as a side container with `BUILDKIT_HOST`
pointing at it. BuildKit pushes on its own behalf and reads registry credentials from the client's
`~/.docker/config.json`, so the GHCR login must be visible to the knock container — a mounted docker
config rather than an ambient one. This is the least-charted integration point in the workflow and
should be proven on a throwaway namespace before the first public run.

The pin lives in `knock.env` as `KNOCK_VERSION`, consumed both as the container tag and as the
checkout ref, so the policies and the binary can never disagree. Renovate bumps it via a
`# renovate: datasource=docker depName=ghcr.io/…/knock` annotation.

### `canary.yml` — nightly, non-blocking

Identical, except: `ref: main`, and destinations land in a **throwaway namespace** never referenced by
the README. The namespace is obtained purely through configuration — `host: ghcr.io/<owner>/canary`
in `KNOCK_REGISTRIES`, yielding `ghcr.io/<owner>/canary/demo/busybox` — so the policy files stay
untouched, exactly as in the showcase run. knock publishes its image only on release, so the canary
builds the image from the `main` checkout (~5 min).

The canary publishes for real rather than running `--dry-run`. A dry-run canary exercises neither the
stamp, nor the SBOM, nor the signature — it detects only parsing and tag-selection regressions, which
is too little to justify a second workflow. Publishing to a throwaway namespace costs the same and
covers the whole chain.

The job is `continue-on-error`: it informs, it never blocks.

## Configuration

```
KNOCK_REGISTRIES={"ghcr":{"host":"ghcr.io/<owner>","username":"…","password":"…"}}
KNOCK_SBOM_FORMATS=spdx-json,cyclonedx-json
KNOCK_ATTEST_SIGNER=keyless
```

Keyless signing is what makes the showcase credible without sharing anything. With `id-token: write`,
cosign signs through Fulcio and the identity recorded in Rekor **is the GitHub workflow's URL**. The
visitor verifies that the image was produced by that workflow — no key, and no trust placed in the
repository owner.

## The thirty-second proof

`verify.sh` is a single file serving two purposes: the CI's final acceptance step, and the block the
visitor copies out of the README. The README therefore cannot promise what the showcase does not do.

```bash
# 1. read the stamp
regctl manifest get ghcr.io/<owner>/demo/debian:bookworm-slim-eu --format '{{json .Annotations}}'

# 2. fetch the package SBOM (attached as an OCI referrer)
regctl artifact list ghcr.io/<owner>/demo/debian:bookworm-slim-eu

# 3. verify the signature — identity is the workflow itself
cosign verify-attestation \
  --certificate-identity-regexp '^https://github.com/<owner>/knock-examples/\.github/workflows/showcase\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<owner>/demo/debian:bookworm-slim-eu
```

The same three commands run against the **bypass** image fail: it carries no stamp, no SBOM, no
signature. That contrast is what the bypass image is for here — a manual, legible counter-example,
not an automated coverage report.

## Prerequisite change in knock — path-prefixed registry hosts

A destination is composed as `{cfg.host}/{project}/{repository}` (`use_cases/reconcile.py:938`), and
`login` passes `cfg.host` verbatim to `regctl registry login` (`adapters/regctl_cli.py:146`). On GHCR
the first path segment must be the owner. So either `host: ghcr.io/<owner>` — and the login breaks —
or `project: <owner>` — and `docs/examples/` files, which say `project: demo`, are no longer replayable
verbatim, destroying the whole point of this design.

**`RegistryConfig.host` must accept a path prefix, with the login using only the host part.** This is a
genuine portability gap in knock rather than a showcase workaround: GHCR, GitLab's registry, and
Artifactory are all path-namespaced. It ships as its own PR, before the showcase repository.

## Deferred — coverage (`knock audit`) has no registry to walk

`audit`, `gc`, and `purge` enumerate through `list_repositories` → `regctl repo ls` → the OCI catalog
API (`use_cases/audit.py:125`). **GHCR does not implement `/v2/_catalog`.** `reconcile` is unaffected
(it addresses repositories named by the policies), but a published coverage report — "X stamped,
Y signed, Z with SBOM, and here is the blind spot" — cannot run on GHCR as things stand.

Resolving it means either teaching `list_repositories` to enumerate through the GitHub REST API when
the OCI catalog is absent, or hosting a catalog-capable registry (Zot). Both are real commitments with
their own trade-offs, and neither should block a showcase that is already valuable without them.
**Coverage is a separate specification.** This one ships the stamp proof.

## Failure modes

The failure that matters: knock has no transaction. If the SBOM or the signature fails *after* the
copy, the image is placed without an inventory — precisely the coverage hole the product claims to
close. A showcase publishing that refutes itself. `verify.sh` as the workflow's final step is the
mitigation: a half-stamped publish turns the job, and the badge, red.

| Failure | Treatment |
|---|---|
| Docker Hub throttling / expired PAT | Explicit named login step, so the red is legible at a glance. This will happen. |
| GHCR lacks the referrers API | regctl falls back to the tag scheme; `regctl artifact list` keeps working, a raw `curl` does not. **Decisive spike before committing** — it determines what the README may promise. |
| Fulcio / Rekor unavailable | The job fails; `verify.sh` guarantees nothing half-published survives. No retry — the schedule is weekly, the next run catches up. |
| Upstream drift (new tags) | Not a failure. Digests move, the stamp follows: the product working. |
| A Renovate knock bump breaks the showcase | The canary warned the night before. |

## Testing

- **In `knock-examples`:** `verify.sh` only. The repository holds no logic, just workflow glue; a test
  suite would have nothing to test.
- **In `knock`:** the path-prefixed `host` change follows the usual TDD cycle — a unit test on
  destination composition, and an integration test against the `regctl` fake-bin asserting that
  `registry login` receives `ghcr.io` alone while the reference is `ghcr.io/<owner>/demo/busybox`.

## Sequencing

The C4 model ships **with this specification**: a third `deploymentEnvironment` — "Showcase — public
proof (GitHub Actions → GHCR)" — alongside greenfield and brownfield, its `DeployShowcase` Mermaid
export, and the view list in `docs/architecture/README.md`. This is a third way knock runs (a CI
runner, no Kubernetes, no ArgoCD), so it is visible at landscape level.

Implementation then follows in order:

1. **Spike:** does GHCR serve the referrers API? It can invalidate a README promise, so it comes first.
2. **knock PR:** path-prefixed registry hosts, with tests. The showcase cannot ship before it.
3. **`knock-examples`:** the repository, both workflows, `verify.sh`, the README. The rebuild path
   (buildkitd + credential propagation) is proven against the throwaway namespace before the first
   public run.
4. **Docs:** a how-to on the knock site pointing at the live showcase, so the adoption surface and the
   proof are linked.
