# 48. Non-registry sources and the `skill` artifact class

Date: 2026-08-29

## Status

Accepted, and implemented on `feat/skill-intake-impl`. A CLI verb is deliberately outside this
slice's scope, so nothing calls the intake path in production yet — see Consequences.

Builds on [1. MirrorPolicy format & reconcile contract](0001-mirror-policy-format.md),
[20. Revision semantics](0020-revision-semantics.md),
[41. `knock verify` — read-side gate](0041-knock-verify-read-side-gate.md).

## Context

An external agent skill is markdown that steers an agent plus scripts that execute on a developer's
machine — two attack surfaces at once, on hosts holding credentials, installed today from anywhere.
Same shape as an external image, one artifact class over. ADR 0001 already called knock a front
door for external OCI **artifacts**; this is the first time that word is exercised.

The governing thesis is that **the label is the product**: the provenance annotations are what the
system exists to make trustworthy, so an inaccurate stamp is worse than no artifact at all. That is
the standard the decisions below are measured against.

Two capabilities were missing — a source that is not a container registry, and an artifact class
that is content rather than a rebuildable image. A third thing is genuinely new: Claude Code cannot
read OCI, so the catalog must be projected into a plugin marketplace manifest.

## Decision

**Schema, additive; `apiVersion` stays `knock.io/v1alpha1`.** `Source = RegistrySource | GitSource`
is a **plain union, not a discriminated one**: every member sets `extra="forbid"` and their required
fields are disjoint, so exactly one can ever match a document — no discriminator field, no version
bump. `skill` joins the existing `ArtifactType` enum; `kind` was never available as the
discriminator, being already `Literal["MirrorPolicy"]`. The source/type rule is deliberately
asymmetric: `image`/`helmChart` registry-only, `skill` git-only, `generic` either.

**`ports/source.py` is generic from the start.** Git is the first non-registry source, not the only
one it should carry; writing it git-specific would make the second artifact class a refactor of a
public contract.

**Source-derived artifacts are stamped without `org.opencontainers.image.base.*`.** There is no
base image and ADR 0020 forbids fabricating one; `revision` carries the upstream commit, which is
what the OCI key means. The cost: a git-sourced stamp **requires a non-empty `KNOCK_LABEL_PREFIX`**
and raises `ConfigError` (exit 3) otherwise, because `coverage.py`'s empty-prefix fallback anchors
on `base.digest`, which a base-less artifact does not have — such a stamp would never read back as
covered. ADR 0041 set the precedent for requiring that setting.

**The archive bound is ≤ 100 MiB**, matching SkillSpector's `INGEST_MAX_BYTES`. An artifact that
cannot be scanned can never pass the gate, so the refusal belongs at intake, where a human is
already looking, rather than on a workstation. It applies to *uncompressed* total size, so summing
declared source sizes is exact — no compression-ratio slack to reason about.

**Intake lands in a new `use_cases/intake.py`**, not as a branch inside `reconcile.py` (1131 lines).
The two paths share no digest bookkeeping. Merging them is deferred and deliberate.

**Two independent defenses against git option injection.** `git fetch` parses options after
positionals, and `ref` reached the adapter straight from policy YAML with no validation. Reproduced
against git 2.54.0 — `git fetch -q --depth 1 origin --upload-pack=<script> v1` **executes
`<script>`**: arbitrary command execution from a policy field, at the front door whose purpose is
to gate untrusted content. Both defenses ship, and the redundancy is the point:

- `--` before the positionals in `GitAdapter.fetch` protects that call site; a future git call that
  forgets it is caught by a test that drives the adapter directly.
- `_GIT_REF_RE` / `_GIT_PATH_RE` on `GitSource` refuse the policy before any adapter runs, naming
  the field the operator has to fix.

Removing either one alone still fails a test. A reader who sees redundancy here should delete
neither. (A third validator on the same surface, `_GIT_URL_RE`, predates this and rejects git's
`ext::` remote-helper syntax, which executes a shell command at clone time.)

**VCS metadata is excluded at the tree walker, not in the git adapter.** The walker is the choke
point every source's tree passes through, so a second `SourcePort` — a tarball, a release zip —
inherits the protection and cannot reintroduce the leak by handing back a dirty tree; a
`git archive`-style clean export would protect git only. Concretely: `git init` plus a depth-1
fetch and checkout of a one-file repository leaves **25 files under `.git/` against 1 file of real
content**, including `.git/config` with the remote URL, which in a real deployment can embed a
credential. `.git` is excluded as an exact name at any depth, as both a directory and a *file* (the
gitfile a worktree or submodule leaves, holding an absolute path on the intake machine).
`.gitignore` and `.gitattributes` are upstream-authored content and are kept.

**The archiver is an injected port, not a direct adapter import.** `intake_skill` already received
`source` and `registry` by injection; reaching into two more adapter modules would have left half
its dependencies invisible in its signature. `ports/archiver.py` records the deliberate
counter-rule: implementations are exercised for real in tests, and **an in-memory implementation
must never be written**. A fake filesystem is what hid the two defects that survived the plan's own
passing tests — the `.git/` leak, and staging the archive inside the tree being packaged. Neither
is reachable through an in-memory tree.

**A plugin marker is accepted at the tree root, or one level inside a single wrapper directory** —
the shape `git archive` and a GitHub release tarball produce, i.e. what a git-sourced intake
yields. The allowance applies only when every path shares the same single root segment; two levels
deep, or two different top-level directories, are still refused.

**The blob digest is reproducible only against a pinned zlib.** `zip_writer` writes an explicit
`ZipInfo` per entry with a fixed `date_time` and `create_system`, so no clock and no TZ leaks into
the bytes. But `ZIP_DEFLATED` output is byte-identical only for a fixed zlib build *and*
compression level, and the level is currently **unpinned**. It cannot be pinned through
`zipfile.ZipFile(compresslevel=…)` — that argument is ignored when writing a prebuilt `ZipInfo`,
which reads the level from `ZipInfo.compress_level`. For a provenance product this is a real
caveat: cross-machine byte-identity of the blob digest depends on running packaging against one
controlled zlib, not on the archive format alone.

**Each new error picks its branch in `errors.py` deliberately.** The table is branch-rooted and
`exit_code_for` walks the MRO, so the choice of parent *is* the exit code — and an exit code is an
operator-facing claim: exit 1 says "your input is wrong" and is a lie when the filesystem or the
registry misbehaved. Hence `ArchiveSizeMismatchError` and `ArchiveSourceReadError` are exit 2
(environmental — re-run against a quiescent tree) while `ArchiveLayoutError` is exit 1, and
`allow_drop` without `previous_count` is `InternalError` (exit 4), not a policy refusal a caller
could confirm away.

## Consequences

- **Every piece exists and is tested, but nothing composes them in production.** `intake_skill` and
  `build_marketplace` have no caller anywhere in `knock/`, and nothing under `knock/cli/` mentions
  intake or skills: **a CLI verb was deliberately left out of this slice**, not missed. Until one
  exists the chain is unreachable by an operator, and the end-to-end integration test is what stands
  in for it — it always runs, driving a real `git fetch` and the real `regctl` binary against an
  `ocidir://` OCI layout with no server and no network. `KNOCK_TEST_REGISTRY` adds an otherwise
  identical networked case as an extra, never as the only path.
- **A `skill` policy in the reconcile directory is reported as failed.** `reconcile` filters
  git-sourced policies out before the plan phase and reports each with `UnsupportedSourceError`, so
  the run exits 1 with status `partial`. Deliberate and non-silent — a skill policy doing nothing
  with no output is the failure mode the design forbids — but a team that adds a skill policy
  before an intake verb exists gets a red scheduled reconcile on every run. The fix is to hold the
  policy out of the reconcile directory, never to soften the report.
- **Not done here, each needing its own plan:** SBOM for skills; the SkillSpector gate and the
  registry blob-read port it needs; upstream drift detection and re-intake; the reviewer diff;
  `knock revoke`; network-egress allowlisting; the reconcile merge; and publishing the marketplace
  manifest plus the managed-settings fragment (Task 7 produces the document; distributing it is a
  fleet operation).
- **Known limits.** The marketplace drop guard is a bare count, so it cannot see *substitution*:
  one skill loses its verdict while another is added, the total is unchanged, and the first vanishes
  from every workstation silently. Seeing that needs `previous_names: frozenset[str]`, which needs a
  caller that can cheaply fetch the prior manifest. Duplicate-name detection is exact-string, so
  `Probe`/`probe` and NFC vs NFD `café` pass and may still collide downstream.
- **The plugin-marker list goes stale silently** if the client's accepted layout changes: a
  correctly-formed skill would be refused with a message listing markers as if they were
  authoritative. The refusal text therefore names its own fallibility and points at the spec.

Full design spec:
[2026-08-29-external-skill-intake-design.md](../../superpowers/specs/2026-08-29-external-skill-intake-design.md)
