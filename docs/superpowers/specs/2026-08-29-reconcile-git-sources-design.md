# Reconciling git sources — design

Bring the intake path inside `reconcile`, so a git-sourced policy is a first-class citizen of the
same convergent run as an image policy rather than an explicit skip.

Builds on [ADR 0048](../../architecture/decisions/0048-non-registry-sources-and-the-skill-artifact-class.md),
which delivered every intake piece and deliberately left the composition out — it lists "the
reconcile merge" as deferred work.

## Context

`intake_skill` fetches a git tree, packages it into a byte-reproducible zip, stamps it and pushes it
as an OCI artifact. It is built, tested end-to-end, and **has no caller**. `reconcile` filters
git-sourced policies out before the plan phase and reports each one `UnsupportedSourceError`
(`use_cases/reconcile.py`), which is honest but leaves the capability unreachable by an operator.

The two paths do not merely differ in plumbing; they differ in *kind*:

| | image path | intake path |
|---|---|---|
| shape | **convergent** — compares source and mirror state, emits import / update / delete | **imperative** — fetch, package, push, unconditionally |
| upstream identity | a list of tags, each resolving to a digest | one ref, resolving to one commit |
| cheap read to plan with | `list_tags(src_repo)` | **none** — `SourcePort` exposes only `fetch` |

Closing that third row is what makes the merge possible at all.

## Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | **Full convergence.** A git policy is a first-class citizen: an unchanged revision pushes nothing, `--dry-run` shows the plan without fetching, and the report distinguishes imported from skipped | Anything less makes `reconcile` non-convergent for one artifact class, and a scheduled run would republish daily for no reason |
| 2 | **Destination tag = resolved revision, plus a moving alias** for the ref name | Reuses the concrete-tags-plus-derived-aliases pattern the image path already has. Convergence becomes "does `sha-<rev>` appear in `list_tags(dest)`" — a read the plan phase already makes, with no extra port call |
| 3 | **`SourcePort` grows `resolve(origin, ref) -> str`** | Forced by 1 + 2: the plan phase must know the revision without materialising a tree, or `--dry-run` would clone. `git ls-remote` is the exact analogue of `list_tags` |
| 4 | **Skills are never deleted.** `archive` and `deletionMode` are **refused** on a `skill` policy, not ignored | The soft-delete pipeline asks a usage oracle "is this still running"; a skill is *installed on workstations*, so the oracle has no answer and deleting a pinned revision breaks an install silently. Refusing tells an author whose policy would not have pruned; ignoring lets them believe it does |
| 5 | **A shared planner protocol**, with `reconcile_policies` reduced to a driver | Chosen over a git branch inside `_apply_plan` (which would add conditions to a 580-line function, half of which never applies to a skill) and over a parallel orchestrator. The cost is a refactor of a working path; §"What moves" is how that cost is bounded |
| 6 | **The image path moves without being rewritten** | A commit that only relocates code is reviewable by eye, and the 1058 existing tests are the net. Any correction to the image path is a separate commit, or does not happen |

## Architecture

### The seam

One planner per **source class**, each taking the batch of policies it owns:

```python
class PolicyPlanner(Protocol):
    """One planner per source class. Not a port: it is an internal orchestration
    seam between use cases, so it does not belong under `ports/`, which is reserved
    for I/O boundaries an adapter implements."""

    def handles(self, policy: MirrorPolicy) -> bool: ...
    def plan(self, policies: list[MirrorPolicy]) -> list[AliasTarget]: ...
    def apply(
        self, *, reporter: Reporter, executor: ThreadPoolExecutor | None
    ) -> list[PolicyReport]: ...
```

Batch-at-a-time rather than policy-at-a-time, so the protocol does not have to carry the twenty
keyword arguments `reconcile_policies` takes today.

**Each planner is constructed with its own dependencies** — the registry planner receives `builder`,
`ca_certs`, `package_mirrors`, `sbom_generator`, `attestor`; the git planner receives `source` and
`archiver`. That is what keeps the protocol small: what differs lives in the constructor, not in the
signature. Each keeps its own internal plan representation (`_Plan` stays private to the registry
planner) and returns to the driver only the `AliasTarget`s, which the driver needs for the global
collision check.

### The driver

`reconcile_policies` becomes:

1. ownership invariant over **all** policies, then the shard filter — unchanged, and already
   source-agnostic: `_resolved_dest_repos` uses only `resolve_imports` and `resolve_registry`
2. partition by `handles`
3. `plan()` each planner
4. `detect_dest_repo_collisions` + `detect_alias_collisions` over the **union** — so an alias
   collision between a git policy and an image policy is caught before any mutation
5. `apply()` each planner, driver-owned executor passed in
6. assemble the `RunReport` — unchanged

`_skipped_source_report` and `UnsupportedSourceError`'s use here both disappear: there is no longer
an unsupported source.

### The git planner

**Plan** (reads only, no mutation):

```
resolve(url, ref) ──▶ revision ──▶ list_tags(dest) ──▶ sha-<rev> present?
                                                          ├── yes ──▶ skipped
                                                          └── no  ──▶ imported + aliased
```

A `ref` that is already 40 hex characters is returned as-is: `ls-remote` lists refs, it does not
resolve an arbitrary object.

**Apply**: `fetch` → `plan_archive` → `write_archive` → `put_artifact` → alias. Under `--dry-run` it
**does not fetch**; it reports the planned operations with `applied=False`. That property is the
whole reason decision 3 exists.

No new operation kinds: `imported`, `aliased` and `skipped` are already in `OperationKind`.

### Report shape

One `TargetReport` per destination, holding one `VariantReport` (name `default`, empty suffix —
matching `intake._IMPLICIT_VARIANT`) with one or two `Operation`s. Variants do not apply: a skill
declares no transform, so there is nothing to fan out.

## What moves

`use_cases/reconcile.py` (1131 lines) becomes four:

| File | Contents |
|---|---|
| `use_cases/policy_planner.py` | the `Protocol`, and nothing else. `AliasTarget` stays in `domain/collision.py` — it is a pure domain type and moving it into the use-case layer would invert the dependency |
| `use_cases/reconcile_registry.py` | today's image path, **relocated verbatim** |
| `use_cases/reconcile_git.py` | the git path, new |
| `use_cases/reconcile.py` | the driver |

Decision 6 is the guard rail: the relocation commit changes no line of `_apply_plan`.

## Error handling

| Case | Behaviour |
|---|---|
| Unresolvable ref, unreachable repository | Fails in `plan()`, before any mutation — same fail-fast contract as the image path's `list_tags` |
| One git policy fails | Reported and isolated; every other policy in the batch still reconciles. Already the image path's invariant |
| `archive` or `deletionMode` on a `skill` policy | `PolicyValidationError` at parse time, like the existing `transform` rule (decision 4) |
| Two policies claiming one destination repo, or one alias | Caught in step 4 across **both** planners, before any mutation |
| Workdir reuse | One workdir per policy: `GitAdapter._claim_workdir` refuses a non-empty directory, so a leftover tree would break the next run |

## Testing

The git planner is testable against the existing fakes plus a `FakeSourcePort`. The cases that carry
the design, each of which fails if a decision is quietly reversed:

1. **Unchanged revision ⇒ `skipped`, and `fetch` is never called.** The convergence claim.
2. **`--dry-run` ⇒ no fetch and no push**, operations reported `applied=False`. Decision 3's purpose.
3. **Moved ref ⇒ new revision tag, alias repointed**, old revision tag untouched. Decisions 2 and 4.
4. **Alias collision between a git policy and an image policy** is detected in the plan phase.
5. **A failing git policy leaves image policies in the same batch reconciled.**
6. **`archive` on a `skill` policy is refused at parse time**, with the surplus never silently kept.

Coverage gates are unchanged: ≥ 80 % global, ≥ 90 % on `knock.domain`.

## Out of scope

- **Deletion, retention and the stability window for skills** — decision 4, refused rather than deferred.
- **Variants for skills.** No transform ⇒ nothing to fan out; a `variants` block on a skill policy is
  a separate question from this merge.
- **SBOM and signing for skills.** Both are listed as deferred in ADR 0048 and neither is needed to
  make the path reachable.
- **Any behavioural change to the image path.** Decision 6.

## Documentation obligations

Per the repository's own rules, this spec is not complete until:

- a thin ADR under `docs/architecture/decisions/` links to it;
- `docs/architecture/workspace.dsl` reflects the new `SourcePort.resolve` and the module split, with
  the Mermaid exports under `docs/architecture/_export/` refreshed;
- `docs/examples/skills/` drops its "designed, not yet runnable" framing and becomes a runnable
  example, and `docs/examples/skills/README.md` loses the reconcile-failure walkthrough;
- `knock-examples`' canary step moves from "expected red" to an ordinary reconcile, and its README
  paragraph moves from *What is not here* into the table.
