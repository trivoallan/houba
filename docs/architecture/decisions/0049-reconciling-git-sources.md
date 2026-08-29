# 49. Reconciling git sources

Date: 2026-08-29

## Status

Accepted, and implemented on `feat/reconcile-git-sources`.

Closes the "reconcile merge" that
[48. Non-registry sources and the `skill` artifact class](0048-non-registry-sources-and-the-skill-artifact-class.md)
listed as deferred. Builds on [1. MirrorPolicy format & reconcile contract](0001-mirror-policy-format.md)
and [20. Revision semantics](0020-revision-semantics.md).

## Context

ADR 0048 delivered every intake piece — the git adapter, the packaging planner, the reproducible zip
writer, the source-derived stamp, `put_artifact` — and deliberately left the composition out.
`intake_skill` had no caller anywhere in `knock/`, and `reconcile` filtered git-sourced policies out
before the plan phase and reported each one `UnsupportedSourceError`. Honest, and non-silent, but the
capability was unreachable by an operator.

The two paths differ in *kind*, not merely in plumbing. The image path is **convergent** — it
compares source and mirror state and emits import / update / delete — against a list of tags, each
resolving to a digest, planned from a cheap `list_tags(src_repo)`. The intake path was
**imperative** — fetch, package, push, unconditionally — against one ref resolving to one commit,
with no cheap read available at all: `SourcePort` exposed only `fetch`. Closing that last gap is
what makes a merge possible rather than a special case bolted onto a convergent orchestrator.

## Decision

**A git policy is a first-class citizen of `reconcile`, fully convergent.** An unchanged revision
transfers nothing, `--dry-run` shows the plan without cloning, and the report distinguishes
`imported` from `aliased` from `skipped`. Anything less would make `reconcile` non-convergent for
one artifact class, and a scheduled run would republish daily for no reason.

**`SourcePort` grows `resolve(origin, ref) -> str`**, which returns the immutable revision *without
materialising a tree* — `git ls-remote`, the exact analogue of `list_tags`. Forced by the
convergence requirement: a plan phase that had to clone every repository in order to say "nothing to
do" would not be a plan phase. The port's contract is that `resolve` and `fetch` must agree for the
same inputs, and `GitAdapter` honours it deliberately (annotated tags peel to their commit; tags
outrank heads, as in `gitrevisions(7)`, because `fetch` resolves them that way too).

**The destination carries an immutable `sha-<revision>` tag per placed revision, plus a moving alias
named for the policy's ref.** This reuses the concrete-tags-plus-derived-aliases pattern the image
path already has, and it is what makes convergence answerable from reads the plan phase was making
anyway.

**Convergence is a conjunction: the revision is placed AND the alias designates it.** Both halves
are load-bearing. A ref can move *backwards* — a revert, a force-push, a release branch reset —
leaving `sha-<revision>` present from an earlier run while the alias still points at the revision
the ref has since abandoned. The first half answers yes there, and a planner stopping at it would
report `skipped` while whoever installs by ref name gets bytes the policy no longer declares. That
is a silent disagreement between the stamped facts and the policy, which is the one failure a
provenance product cannot ship. The second half costs one `get_annotations` per destination, paid
only where it can change the outcome — never when nothing is placed (the alias moves regardless),
and never when the alias is absent from the free `list_tags` result. A stale alias is repaired by
copying the revision tag already in place, never by re-fetching.

**A ref that moves between `resolve` and `fetch` refuses the placement.** The plan derives
`sha-<revision>` from the resolve; the apply fetches by the moving `ref`, because `fetch` is the only
way to materialise a tree and fetching a bare sha requires `uploadpack.allowReachableSHA1InWant`,
off by default on most servers — pinning the fetch would trade a rare silent corruption for a common
hard failure. The window is closed on the other side instead: `intake_skill` compares what the fetch
landed on against the caller's `expected_revision` and raises `SourceRevisionMismatchError` (an
`AdapterError`, exit 2) *before* the stamp, the packaging or the push, so nothing is placed and the
next run converges. The check sits in `intake_skill` rather than in the planner precisely so it runs
before the push, and so every future caller inherits it.

**Retention is refused on a skill policy, not ignored.** `archive` and `deletionMode` raise
`PolicyValidationError` at parse time, like the existing `transform` rule. The soft-delete pipeline
asks a usage oracle "is this still running in production"; a skill is installed on *workstations*,
where the oracle has no answer, and deleting a revision someone pinned breaks their install
silently. Refusing tells an author whose policy would not have pruned; ignoring lets them believe it
does.

**A shared `PolicyPlanner` protocol, with `reconcile_policies` reduced to a driver.** One planner
per source class (`handles` / `plan` / `apply`), each constructed with its own dependencies — the
registry planner takes `builder`, `ca_certs`, `package_mirrors`, `sbom_generator`, `attestor`; the
git planner takes `source` and `archiver`. That is what keeps the protocol to three methods instead
of the twenty keyword arguments `reconcile_policies` used to carry. Chosen over a git branch inside
`_apply_plan`, which would have added conditions to a 580-line function half of which never applies
to a skill. It is **not** a port: it is an internal orchestration seam between use cases, and
`ports/` is reserved for I/O boundaries an adapter implements.

The driver now: enforces the ownership invariant over all policies and applies the shard filter
(both already source-agnostic), partitions by `handles`, calls `plan()` on each planner, runs
`detect_dest_repo_collisions` + `detect_alias_collisions` over the **union** of the returned
`AliasTarget`s — so a collision between a git policy and an image policy is caught before any
mutation — then calls `apply()` on each and assembles the `RunReport`.

**The image path moved without being rewritten.** `use_cases/reconcile.py` became four modules:
`policy_planner.py` (the protocol and nothing else), `reconcile_registry.py` (today's image path,
relocated verbatim), `reconcile_git.py` (new), and `reconcile.py` (the driver). The relocation
commit changes no line of `_apply_plan`, so it is reviewable by eye and the existing tests are the
net.

**C4 model: updated.** `SourcePort` and its `resolve` operation, and the `use_cases` split, are
reflected in the Container, Hexagon and Component views of `workspace.dsl`, with the Mermaid exports
under `docs/architecture/_export/` refreshed.

## Consequences

- **A skill policy now reconciles in the same run as an image policy**, from the same directory,
  with the same report shape — one `TargetReport` per destination holding a single `VariantReport`
  named `default`. `docs/examples/skills/` is a runnable example rather than a design document.
- **Revision tags accumulate without bound.** Skills are never deleted, so every revision a policy
  has ever placed stays in the destination repository forever. A long-lived policy tracking a busy
  branch grows one tag per placement. This is the deliberate cost of decision "retention is
  refused": whoever pinned `sha-<revision>` keeps resolving it. Archives are small, but the tag list
  is unbounded and registry storage should be budgeted for it.
- **`UnsupportedSourceError` is gone.** It had no production caller once a planner claimed every
  source class, because there is no longer an unsupported source. It was removed in its own commit,
  kept separate so this branch's behavioural diff stayed readable. Anything catching it by name —
  there was nothing in this repository — would need updating; it never appeared in a public
  contract, only in a `PolicyReport.error.type` string for a run shape that can no longer occur.
- **Not done here, each needing its own plan:** a standalone CLI verb for a one-off intake (the
  reconcile path is now the only caller); variants for skills — no transform means nothing to fan
  out, and a `variants` block on a skill policy is a separate question; SBOM and signing for skills,
  both listed as deferred in ADR 0048 and neither needed to make the path reachable; deletion,
  retention and a stability window for skills, refused rather than deferred; and any behavioural
  change to the image path, which this branch deliberately does not touch.
- **The window between `resolve` and `fetch` is closed, not eliminated.** A moved ref costs a wasted
  fetch and a failed policy rather than a corrupt placement. The failure is isolated to its own
  policy — every other policy in the batch still reconciles — and the next scheduled run converges
  on the ref's new tip with no operator action.

Full design spec:
[2026-08-29-reconcile-git-sources-design.md](../../superpowers/specs/2026-08-29-reconcile-git-sources-design.md)
