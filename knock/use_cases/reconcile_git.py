"""The git-sourced reconcile path: resolve, compare, then package and place.

Convergence without a tag list: the destination carries one immutable tag per placed
revision (`sha-<rev>`) plus a moving alias for the ref name, so "is this already placed"
is a `list_tags` read — the same cheap plan-phase read the registry path makes — and no
second port call is needed.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from knock.config import RegistryConfig, resolve_registry
from knock.domain.collision import AliasTarget
from knock.domain.mirror_policy import GitSource, MirrorPolicy
from knock.domain.policy_merge import resolve_imports
from knock.errors import InternalError, exit_code_for
from knock.ports.archiver import ArchiverPort
from knock.ports.registry import RegistryPort
from knock.ports.reporter import Counts, ErrorInfo, OperationEvent, Reporter
from knock.ports.source import SourcePort
from knock.use_cases.intake import _IMPLICIT_VARIANT, IntakeRequest, intake_skill
from knock.use_cases.registry_session import ensure_registry_session
from knock.use_cases.report import (
    Operation,
    PolicyReport,
    TargetReport,
    VariantReport,
    counts_of,
    merge_counts,
    node_status,
)

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
        by_policy: dict[str, list[_GitPlan]] = defaultdict(list)
        for plan in self._plans:
            by_policy[plan.policy.metadata.name].append(plan)

        reports: list[PolicyReport] = []
        for plans in by_policy.values():
            policy_name = plans[0].policy.metadata.name
            source_ref = plans[0].source.url
            reporter.policy_started(policy_name, source_ref)
            try:
                targets = [self._apply_one(p, reporter=reporter) for p in plans]
                all_ops = [op for t in targets for v in t.variants for op in v.operations]
                totals = merge_counts([t.totals for t in targets])
                reporter.policy_completed(policy_name, totals)
                reports.append(
                    PolicyReport(
                        name=policy_name,
                        source=source_ref,
                        status=node_status(all_ops),
                        error=None,
                        totals=totals,
                        targets=targets,
                    )
                )
            except Exception as exc:
                # Isolation, as on the image path: one destination refusing a push
                # fails its own policy and leaves the rest of the batch reconciled.
                info = ErrorInfo(
                    type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc)
                )
                reporter.policy_failed(policy_name, info)
                reports.append(
                    PolicyReport(
                        name=policy_name,
                        source=source_ref,
                        status="failed",
                        error=info,
                        totals=Counts(),
                        targets=[],
                    )
                )
        return reports

    def _apply_one(self, plan: _GitPlan, *, reporter: Reporter) -> TargetReport:
        if plan.already_placed:
            # Nothing to transfer: the revision tag is immutable, so its presence is
            # the whole convergence answer. No fetch, no push.
            skipped = Operation(
                kind="skipped",
                out_tag=plan.revision_tag,
                src_tag=plan.ref,
                digest=plan.revision,
                applied=True,
            )
            self._emit(plan, skipped, reporter=reporter)
            return _target_report(plan, [skipped])

        if self.dry_run_tags:
            # No fetch and no push — the property `SourcePort.resolve` exists for.
            return _target_report(
                plan,
                [
                    Operation(
                        kind="imported",
                        out_tag=plan.revision_tag,
                        src_tag=plan.ref,
                        digest=plan.revision,
                        applied=False,
                    ),
                    Operation(
                        kind="aliased",
                        out_tag=plan.ref,
                        src_tag=plan.revision_tag,
                        digest=plan.revision,
                        applied=False,
                    ),
                ],
            )

        ensure_registry_session(self.registry, plan.config, self.logged_in)
        if self.work_dir is not None:
            self.work_dir.mkdir(parents=True, exist_ok=True)
        revision_ref = f"{plan.dest_repo}:{plan.revision_tag}"
        with tempfile.TemporaryDirectory(prefix="knock-intake-", dir=self.work_dir) as tmp:
            result = intake_skill(
                IntakeRequest(
                    origin=plan.source.url,
                    ref=plan.ref,
                    path=plan.source.path,
                    destination_ref=revision_ref,
                    title=plan.policy.metadata.name,
                    policy=plan.policy.metadata.name,
                    import_name=plan.import_name,
                    owners=plan.owners,
                    vendor=plan.vendor,
                    # A subdirectory of the temp dir, never the temp dir itself:
                    # `GitAdapter._claim_workdir` refuses a workdir it did not create.
                    workdir=Path(tmp) / "src",
                ),
                source=self.source,
                registry=self.registry,
                archiver=self.archiver,
                prefix=self.label_prefix,
                now=self.now,
            )
        # The alias moves after the revision tag exists, so a reader following the ref
        # name never resolves to a tag that is not there yet.
        self.registry.copy(revision_ref, f"{plan.dest_repo}:{plan.ref}")
        operations = [
            Operation(
                kind="imported",
                out_tag=plan.revision_tag,
                src_tag=plan.ref,
                digest=result.revision,
                applied=True,
                out_digest=result.manifest_digest,
            ),
            Operation(
                kind="aliased",
                out_tag=plan.ref,
                src_tag=plan.revision_tag,
                digest=result.revision,
                applied=True,
                out_digest=result.manifest_digest,
            ),
        ]
        for op in operations:
            self._emit(plan, op, reporter=reporter)
        return _target_report(plan, operations)

    def _emit(self, plan: _GitPlan, op: Operation, *, reporter: Reporter) -> None:
        reporter.operation_applied(
            OperationEvent(
                policy=plan.policy.metadata.name,
                dest_repo=plan.dest_repo,
                variant=_IMPLICIT_VARIANT,
                kind=op.kind,
                out_tag=op.out_tag,
                src_tag=op.src_tag,
                digest=op.digest,
                applied=op.applied,
                out_digest=op.out_digest,
            )
        )


def _target_report(plan: _GitPlan, operations: list[Operation]) -> TargetReport:
    """One destination, one variant. A skill declares no transform, so there is nothing
    to fan out — the single implicit variant is spelled as `intake` spells it."""
    totals = counts_of(operations)
    status = node_status(operations)
    return TargetReport(
        dest_repo=plan.dest_repo,
        status=status,
        variants=[
            VariantReport(
                name=_IMPLICIT_VARIANT,
                suffix="",
                status=status,
                totals=totals,
                operations=operations,
            )
        ],
        operations=[],
        totals=totals,
    )
