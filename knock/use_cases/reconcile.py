"""Reconcile use case: orchestrate selection → expand → reconcile → apply against
real registries. Tags are mirrored by copy, or rebuilt through a hardening
transform, then stamped. Returns a structured RunReport and emits in-flight
events through the Reporter port. Depends only on ports.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from knock.config import (
    CACertSource,
    PackageMirror,
    RegistryConfig,
    resolve_registry,
)
from knock.domain.collision import (
    AliasTarget,
    detect_alias_collisions,
    detect_dest_repo_collisions,
)
from knock.domain.deletion_mode import DeletionMode
from knock.domain.mirror_policy import Archive, MirrorPolicy
from knock.domain.policy_merge import resolve_imports
from knock.domain.sharding import owns
from knock.errors import InternalError
from knock.ports.archiver import ArchiverPort
from knock.ports.attestor import AttestorPort
from knock.ports.image_builder import ImageBuilderPort
from knock.ports.registry import RegistryPort
from knock.ports.reporter import Reporter
from knock.ports.sbom import SbomGeneratorPort
from knock.ports.source import SourcePort
from knock.use_cases.policy_planner import PolicyPlanner
from knock.use_cases.reconcile_git import GitPlanner
from knock.use_cases.reconcile_registry import RegistryPlanner
from knock.use_cases.report import (
    PolicyReport,
    RunMode,
    RunReport,
    RunStatus,
    merge_counts,
)


def _resolved_dest_repos(policy: MirrorPolicy, roster: dict[str, RegistryConfig]) -> list[str]:
    """Every destination repo a policy writes to, resolved against the roster.
    Pure: uses resolve_imports + resolve_registry only (no expand, no registry calls)."""
    repos: list[str] = []
    for resolved in resolve_imports(policy.spec):
        for dest in resolved.destinations or []:
            _name, cfg = resolve_registry(dest.registry, roster)
            repos.append(f"{cfg.host}/{dest.project}/{dest.repository}")
    return repos


def reconcile_policies(
    policies: list[MirrorPolicy],
    *,
    registry: RegistryPort,
    builder: ImageBuilderPort,
    source: SourcePort,
    archiver: ArchiverPort,
    roster: dict[str, RegistryConfig],
    ca_certs: dict[str, CACertSource],
    package_mirrors: dict[str, PackageMirror],
    build_platform: str,
    now: datetime,
    label_prefix: str,
    dry_run_tags: bool,
    dry_run_deletions: bool,
    reporter: Reporter,
    deletion_mode: DeletionMode = DeletionMode.purge,
    work_dir: Path | None = None,
    max_concurrency: int = 1,
    shard_index: int = 0,
    shard_count: int = 1,
    attestor: AttestorPort | None = None,
    attest_builder_id: str = "",
    sbom_generator: SbomGeneratorPort | None = None,
    sbom_formats: list[str] | None = None,
    retention_global: Archive | None = None,
) -> RunReport:
    mode: RunMode = "dry-run" if (dry_run_tags or dry_run_deletions) else "apply"
    sbom_formats = sbom_formats or []

    # --- Ownership invariant over ALL policies (pure, no I/O), then shard filter. ---
    # Every pod sees the full policy set (git-synced) and enforces one-owner-per-repo
    # identically; it then applies only the policies it owns. shard_count == 1 ⇒ all.
    owners = [
        (repo, policy.metadata.name)
        for policy in policies
        for repo in _resolved_dest_repos(policy, roster)
    ]
    detect_dest_repo_collisions(owners)
    policies = [
        p
        for p in policies
        if owns(p.metadata.name, shard_index=shard_index, shard_count=shard_count)
    ]

    # --- Dispatch: one planner per source class, chosen by `handles`. ---
    # A list, not a special case: a new source class appends its planner here and the
    # rest of this function is unchanged.
    planners: list[PolicyPlanner] = [
        RegistryPlanner(
            registry=registry,
            builder=builder,
            roster=roster,
            ca_certs=ca_certs,
            package_mirrors=package_mirrors,
            build_platform=build_platform,
            now=now,
            label_prefix=label_prefix,
            dry_run_tags=dry_run_tags,
            dry_run_deletions=dry_run_deletions,
            deletion_mode=deletion_mode,
            work_dir=work_dir,
            attestor=attestor,
            attest_builder_id=attest_builder_id,
            sbom_generator=sbom_generator,
            sbom_formats=sbom_formats,
            retention_global=retention_global,
        ),
        GitPlanner(
            registry=registry,
            source=source,
            archiver=archiver,
            roster=roster,
            now=now,
            label_prefix=label_prefix,
            dry_run_tags=dry_run_tags,
            work_dir=work_dir,
        ),
    ]
    batches = [(pl, [p for p in policies if pl.handles(p)]) for pl in planners]
    unclaimed = [p for p in policies if not any(pl.handles(p) for pl in planners)]
    if unclaimed:
        # Every member of the `Source` union has a planner, so this is a third member
        # added without one — a bug, not user input, and not something to report as a
        # skip: a policy silently doing nothing is the failure mode a coverage tool can
        # least afford.
        raise InternalError(f"no planner handles policy '{unclaimed[0].metadata.name}'")

    # --- Plan phase (fail-fast): every planner plans its batch, then ONE collision
    # check across all of them. Planning mutates nothing, so config errors (unknown
    # cert/mirror names, unreadable cert files) and alias collisions surface before
    # ANY mutation, whichever planner would have made it. ---
    alias_entries: list[AliasTarget] = []
    for planner, batch in batches:
        if batch:
            alias_entries += planner.plan(batch)
    detect_alias_collisions(alias_entries)  # fail fast before ANY mutation

    # --- Apply phase (isolated per policy). ---
    reporter.run_started(len(policies), mode=mode)
    policy_reports: list[PolicyReport] = []
    with ExitStack() as stack:
        executor: ThreadPoolExecutor | None = (
            stack.enter_context(ThreadPoolExecutor(max_workers=max_concurrency))
            if max_concurrency > 1
            else None
        )
        for planner, batch in batches:
            if batch:
                policy_reports += planner.apply(reporter=reporter, executor=executor)

    statuses = [p.status for p in policy_reports]
    if all(s == "ok" for s in statuses):
        status: RunStatus = "ok"
    elif all(s == "failed" for s in statuses):
        status = "failed"
    else:
        status = "partial"
    report = RunReport(
        mode=mode,
        status=status,
        totals=merge_counts([p.totals for p in policy_reports]),
        policies=policy_reports,
    )
    reporter.run_completed(report)
    return report
