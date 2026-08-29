# External agent skill — a front door for source-derived artifacts

This example places one **real, public** agent skill — [`mcp-builder`](https://github.com/anthropics/skills/tree/main/skills/mcp-builder),
from Anthropic's public skills repository — through knock's **intake path**: the repository is
fetched at an immutable commit, the named sub-tree is packaged into a byte-reproducible zip, stamped
with provenance, and pushed as an OCI artifact (`application/vnd.knock.skill.v1`) a developer's
client installs from.

It is the same thesis as an image policy — one declared upstream, one placement, one stamp — applied
to an artifact that has no base image and no tag list.

The numbers below are measured against that tree, not asserted: `plan_archive` on
`skills/mcp-builder` at `3b3fad96` yields **10 entries, 121,756 bytes — 0.12% of the 100 MiB
bound**, with no symlink, no escape and no collision, and the root `SKILL.md` satisfying the layout
check.

## Running it

```bash
knock reconcile docs/examples/skills --dry-run   # the plan, with no clone and no push
knock reconcile docs/examples/skills
```

It needs `git` on `PATH` and a non-empty `KNOCK_LABEL_PREFIX` (see *What gets stamped* below). A
skill policy sits in the same directory as an image policy and reconciles in the same run.

## Two tags per placement, and why convergence is a conjunction

A git source has no tag list to compare against — one ref, resolving to one commit. So the
destination carries **two** tags for each placement:

| Tag | Kind | Holds |
|---|---|---|
| `sha-<revision>` | **immutable** — written once, never moved, never deleted | the artifact built from exactly that commit |
| the ref name (`main`, `v1.2.0`, …) | a **moving alias**, copied onto the revision tag | whatever revision the policy currently declares |

Convergence is therefore *the revision is placed **and** the alias designates it* — not just the
first half. The plan phase reads both cheaply: `SourcePort.resolve` (`git ls-remote`, no clone) for
the revision, `list_tags` on the destination for what is already there, and — only when the revision
is already placed, where it can still change the outcome — one `get_annotations` on the alias, whose
`org.opencontainers.image.revision` names the revision it points at.

That gives four behaviours, all of them observable:

| Situation | What a run does |
|---|---|
| **First run** | Fetches, packages, pushes to `sha-<revision>`, then copies that onto the ref-name alias. `imported` + `aliased` |
| **Second run, unchanged upstream** | Nothing at all — no fetch, no push, not even a re-point. `skipped` |
| **The ref moved forward** | Places a **new** `sha-<revision>` tag and repoints the alias onto it. The old revision tag stays exactly where it was |
| **The ref moved backwards** (a revert, a force-push, a release branch reset) | The revision is already placed, so nothing is fetched or pushed; the alias is repointed onto the tag already there. `aliased` alone |

The last row is why the second half of the condition is load-bearing. A planner that stopped at "is
`sha-<revision>` present?" would answer yes and report `skipped`, while whoever installs by ref name
keeps getting the revision the policy has since abandoned — a silent disagreement between the
stamped facts and the policy, in the one product whose claim is that those facts can be trusted.

An alias carrying no revision annotation — placed by hand, or by something that is not knock — reads
as stale and is repointed onto what the policy declares. That is self-healing: the copy puts the
stamped manifest there, so the next run reads it and skips.

**A ref that moves *during* a run is refused, not placed.** The plan resolves once and derives
`sha-<revision>` from the answer; if the fetch then lands on a different commit, the placement is
abandoned before anything is stamped or pushed (`SourceRevisionMismatchError`, exit 2), and the next
run converges on the ref's new tip. An immutable tag holding a different revision would be exactly
the lie the stamp exists to prevent.

## Revision tags accumulate, and that is the design

Skills are **never deleted** — `archive` and `deletionMode` are refused on a skill policy rather
than ignored. So every revision a policy has ever placed stays in the destination repository
forever, and a long-lived policy on a busy branch accumulates one tag per placement.

This is deliberate. The soft-delete pipeline asks a usage oracle "is this still running in
production"; a skill is installed on *workstations*, where the oracle has no answer, and pruning a
revision someone pinned breaks their install with no warning. Whoever pinned `sha-<revision>` keeps
resolving it. Budget registry storage accordingly; a skill archive is small (this one is 121,756
bytes) but the tag list is unbounded.

## What the policy has to say, and what it must not

| Rule | Why |
|---|---|
| `artifactType: skill` **requires** a git source | Enforced in the schema. `image` and `helmChart` are the mirror rule — registry only. `generic` deliberately accepts either, so a later artifact class can be git-sourced without a schema change |
| `tags: {}` is required and **read by nothing** | Selection for a git source is the `ref`, not a tag regex. The empty mapping is the honest spelling — a regex here would look like it selects something |
| **No `transform:`**, anywhere in the policy | A skill is placed as published. There is no base image to re-root onto, so hardening has nothing to act on; declaring a step is a validation error, not a silent no-op |
| **No `archive:` and no `deletionMode:`** | Skills are never deleted — see above. Refused rather than ignored, for the same reason as `transform`: an author who declares retention believes their policy prunes, and it never will |
| `path:` is optional, and does real work here | It re-roots the tree onto `skills/mcp-builder`, so one skill is placed rather than the whole 4.3 MiB monorepo. The layout check then runs against the *re-rooted* tree — which is why the sub-directory's own `SKILL.md` is what satisfies it |
| `ref:` may be a branch, a tag, or a commit | All three resolve to an immutable sha before anything is packaged, and the resolved value is what is stamped. The example pins a commit because a provenance product should show the immutable case |

## What gets stamped, and what deliberately does not

The stamp is **base-less**. `org.opencontainers.image.base.name` and `.base.digest` are omitted
rather than fabricated — [ADR 0020](../../architecture/decisions/0020-revision-semantics.md)'s rule,
applied to a case it did not originally cover. `org.opencontainers.image.revision` carries the
resolved upstream commit, which is exactly what the OCI key means: the SCM revision of the packaged
software.

Because there is no base image, the empty-prefix fallback that `is_stamped` relies on elsewhere has
nothing to anchor to — the OCI-standard keys alone would be indistinguishable from an unstamped
artifact. So intake **requires** a non-empty `KNOCK_LABEL_PREFIX` and refuses with a `ConfigError`
(exit 3) rather than placing an artifact whose provenance cannot later be detected.

## The load-bearing identity is the blob digest

The zip's sha256 **is** the OCI blob digest of the artifact's single layer, and it is what the
marketplace manifest pins and the client verifies on install. That equality is asserted end-to-end
in `tests/integration/test_skill_intake_e2e.py` against a real `git fetch` and a real `regctl`
against an on-disk OCI layout — no server, no docker, no network — so it runs on every `pytest`.

One honest caveat, recorded in the ADR: the digest is reproducible only against a pinned zlib. The
compression level cannot be pinned through `zipfile` for a prebuilt `ZipInfo`, so a different zlib
build can produce a different blob for identical inputs. For a provenance product that is a real
limit, not a footnote.

## What intake refuses before anything is pushed

Packaging is a pure function, so every refusal is testable without a filesystem — and all of them
fire before a single byte reaches the registry:

- **symlinks**, determined without following the link, so a link cannot smuggle in a file from
  outside the tree;
- **paths that escape the root**, and **paths containing a backslash** — the latter is not an escape
  under POSIX, but would become one wherever `\` separates path components;
- **colliding archive members**, including a path listed twice;
- **archives over 100 MiB**, bounded here so the failure happens at intake where a human is looking;
- **trees with no recognisable plugin layout**.

VCS metadata is excluded at the tree walker rather than in the git adapter, so a second source
implementation inherits the protection instead of having to re-earn it. This matters more than it
sounds: a naive walk of a freshly fetched repository archives `.git/config`, which carries the remote
URL and can embed a credential.

## Related

- [ADR 0048 — Non-registry sources and the skill artifact class](../../architecture/decisions/0048-non-registry-sources-and-the-skill-artifact-class.md) — the intake path itself
- [ADR 0049 — Reconciling git sources](../../architecture/decisions/0049-reconciling-git-sources.md) — the tag scheme and convergence rules above
- [`docs/examples/reference/`](../reference/) — the registry-sourced equivalents
