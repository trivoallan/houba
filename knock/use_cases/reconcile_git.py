"""The git-sourced reconcile path: resolve, compare, then package and place.

Convergence without a tag list: the destination carries one immutable tag per placed
revision (`sha-<rev>`) plus a moving alias for the ref name, so "is this already placed"
is a `list_tags` read — the same cheap plan-phase read the registry path makes — and no
second port call is needed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from knock.config import RegistryConfig, resolve_registry
from knock.domain.collision import AliasTarget
from knock.domain.mirror_policy import GitSource, MirrorPolicy
from knock.domain.policy_merge import resolve_imports
from knock.errors import InternalError
from knock.ports.archiver import ArchiverPort
from knock.ports.registry import RegistryPort
from knock.ports.reporter import Reporter
from knock.ports.source import SourcePort
from knock.use_cases.report import PolicyReport

REVISION_TAG_PREFIX = "sha-"


@dataclass(frozen=True)
class _GitPlan:
    """One destination of one import, with everything `apply` needs to place it.

    Carries the resolved revision rather than re-resolving in `apply`: the plan phase
    decided `already_placed` against *this* value, and a second `ls-remote` could
    answer differently for a moving ref — placing bytes the decision never described.
    """

    policy: MirrorPolicy
    source: GitSource
    import_name: str
    owners: list[str] | None
    vendor: str | None
    dest_repo: str
    config: RegistryConfig
    revision: str
    ref: str
    already_placed: bool

    @property
    def revision_tag(self) -> str:
        return f"{REVISION_TAG_PREFIX}{self.revision}"


@dataclass
class GitPlanner:
    """The git-sourced planner: resolve every policy it owns, then place what is missing.

    Satisfies `PolicyPlanner` structurally. The dependency split follows the rule that
    protocol states: `reporter` and `executor` are parameters of `apply` because the
    driver owns them, while `source` and `archiver` — which only this planner needs —
    are constructor fields.
    """

    registry: RegistryPort
    source: SourcePort
    archiver: ArchiverPort
    roster: dict[str, RegistryConfig]
    now: datetime
    label_prefix: str
    dry_run_tags: bool
    work_dir: Path | None = None
    # Shared with the other planners by the driver — see RegistryPlanner.logged_in: a
    # per-planner set would re-login hosts a sibling planner just configured.
    logged_in: set[str] = field(default_factory=set)
    # None until `plan` runs — see the guard in `apply`. `init=False` keeps it out of
    # the constructor, `repr=False` out of every log line and exception repr (it holds
    # each MirrorPolicy and RegistryConfig in the batch).
    _plans: list[_GitPlan] | None = field(default=None, init=False, repr=False)

    def handles(self, policy: MirrorPolicy) -> bool:
        return isinstance(policy.spec.source, GitSource)

    def plan(self, policies: list[MirrorPolicy]) -> list[AliasTarget]:
        aliases: list[AliasTarget] = []
        plans: list[_GitPlan] = []
        for policy in policies:
            src = policy.spec.source
            if not isinstance(src, GitSource):
                # The driver partitions its worklist by `handles` before planning, so a
                # registry source here is a bug in that partition, not user input.
                raise InternalError(
                    f"policy '{policy.metadata.name}' has a registry source; "
                    "GitPlanner only reconciles git sources"
                )
            # Resolve once per policy, never fetch: this is the read that makes a dry
            # run a dry run rather than a clone of every repository.
            revision = self.source.resolve(src.url, src.ref)
            revision_tag = f"{REVISION_TAG_PREFIX}{revision}"
            for resolved in resolve_imports(policy.spec):
                for dest in resolved.destinations or []:
                    _name, cfg = resolve_registry(dest.registry, self.roster)
                    dest_repo = f"{cfg.host}/{dest.project}/{dest.repository}"
                    already_placed = revision_tag in self.registry.list_tags(dest_repo)
                    plans.append(
                        _GitPlan(
                            policy=policy,
                            source=src,
                            import_name=resolved.name,
                            owners=list(resolved.owners) if resolved.owners else None,
                            vendor=resolved.vendor,
                            dest_repo=dest_repo,
                            config=cfg,
                            revision=revision,
                            ref=src.ref,
                            already_placed=already_placed,
                        )
                    )
                    aliases.append(
                        AliasTarget(dest_repo=dest_repo, alias=src.ref, target=revision_tag)
                    )
        # Assign, never append: a second `plan` call replaces the batch rather than
        # silently doubling the work a later `apply` would do.
        self._plans = plans
        return aliases

    def apply(
        self, *, reporter: Reporter, executor: ThreadPoolExecutor | None
    ) -> list[PolicyReport]:
        if self._plans is None:
            # A driver that applies a batch it never planned would report a clean
            # empty run — silently placing nothing. Loud instead.
            raise InternalError("GitPlanner.apply called before plan")
        raise NotImplementedError
