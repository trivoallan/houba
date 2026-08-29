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

## Not yet runnable, and the example says so

Every piece exists and is tested: the packaging planner, the reproducible zip writer, the git
adapter, `put_artifact`, the source-derived stamp, and the composition in `use_cases/intake.py`.
**What is missing is the CLI verb** that runs them in production.

So this example documents the design and does not yet execute. Pointing `knock reconcile` at this
directory produces an explicit failure rather than a silent no-op:

```
policy 'mcp-builder' is git-sourced; not handled by reconcile
```

`UnsupportedSourceError` (a `DomainError`, exit 1), reported with `status=failed` while every
registry-sourced policy in the same run reconciles normally. A team adopting a skill policy before
the verb lands gets a red scheduled run, which is the point: the alternative is a green run that
mirrored nothing.

## What the policy has to say, and what it must not

| Rule | Why |
|---|---|
| `artifactType: skill` **requires** a git source | Enforced in the schema. `image` and `helmChart` are the mirror rule — registry only. `generic` deliberately accepts either, so a later artifact class can be git-sourced without a schema change |
| `tags: {}` is required and **read by nothing** | Selection for a git source is the `ref`, not a tag regex. The empty mapping is the honest spelling — a regex here would look like it selects something |
| **No `transform:`**, anywhere in the policy | A skill is placed as published. There is no base image to re-root onto, so hardening has nothing to act on; declaring a step is a validation error, not a silent no-op |
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

- [`docs/architecture/decisions/0048-non-registry-sources-and-the-skill-artifact-class.md`](../../architecture/decisions/0048-non-registry-sources-and-the-skill-artifact-class.md) — the ADR, including the deferred work
- [`docs/examples/reference/`](../reference/) — the registry-sourced equivalents
