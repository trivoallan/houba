"""The reference reaper driver (§7): scan pending marks, judge by prod usage, purge.

Isolated from reconcile's orchestration — reuses only RegistryPort. Fail-closed:
oracle error / uncertainty => protect. Partial-failure: per-candidate hard errors
are recorded and redden the exit; they never block siblings. The catalog walk is
SEQUENTIAL in v1.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from knock.config import RegistryConfig, resolve_registry
from knock.domain.lifecycle import PENDING_DELETION_ARTIFACT_TYPE, parse_pending_mark
from knock.domain.purge import PurgeDecision, decide_purge, usage_window_start
from knock.domain.scan.refs import is_referrers_fallback_tag
from knock.errors import KnockError, exit_code_for
from knock.ports.registry import Referrer, RegistryPort
from knock.ports.reporter import ErrorInfo
from knock.ports.usage_oracle import UsageOraclePort, UsageQuery
from knock.use_cases.registry_session import ensure_registry_session, walk_repo_refs

PurgeMode = Literal["apply", "dry-run"]


class PurgeOutcome(BaseModel):
    image_ref: str
    digest: str | None = None
    decision: str | None = None  # "purge" | "protect" | "uncertain"
    reason: str = ""
    applied: bool = False
    error: ErrorInfo | None = None  # set => a hard failure processing this candidate


class PurgeReport(BaseModel):
    mode: PurgeMode
    outcomes: list[PurgeOutcome]


def purge_exit_code(report: PurgeReport) -> int:
    """0 unless a candidate hit a hard error; then the worst failure exit code."""
    codes = [o.error.exit_code for o in report.outcomes if o.error is not None]
    return max(codes) if codes else 0


def _err(exc: BaseException) -> ErrorInfo:
    return ErrorInfo(type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc))


def purge_marks(
    *,
    registry: RegistryPort,
    oracle: UsageOraclePort,
    roster: dict[str, RegistryConfig],
    only_registry: str | None,
    label_prefix: str,
    min_idle_days: int,
    now: datetime,
    apply: bool,
) -> PurgeReport:
    mode: PurgeMode = "apply" if apply else "dry-run"
    since = usage_window_start(now, timedelta(days=min_idle_days))

    if only_registry is not None:
        name, cfg = resolve_registry(only_registry, roster)
        targets = [(name, cfg)]
    else:
        targets = list(roster.items())

    outcomes: list[PurgeOutcome] = []
    logged_in: set[str] = set()
    for _name, cfg in targets:
        ensure_registry_session(registry, cfg, logged_in)
        for repo_ref in walk_repo_refs(registry, cfg):
            # A `sha256-<digest>` tag is a referrer manifest stored under the referrers-tag
            # schema (registries without the referrers API), not an image — skip it.
            for tag in registry.list_tags(repo_ref):
                if is_referrers_fallback_tag(tag):
                    continue
                image_ref = f"{repo_ref}:{tag}"
                for ref in registry.list_referrers(image_ref, PENDING_DELETION_ARTIFACT_TYPE):
                    outcomes.append(
                        _process(
                            repo_ref=repo_ref,
                            image_ref=image_ref,
                            referrer=ref,
                            registry=registry,
                            oracle=oracle,
                            label_prefix=label_prefix,
                            since=since,
                            apply=apply,
                        )
                    )
    return PurgeReport(mode=mode, outcomes=outcomes)


def _process(
    *,
    repo_ref: str,
    image_ref: str,
    referrer: Referrer,
    registry: RegistryPort,
    oracle: UsageOraclePort,
    label_prefix: str,
    since: datetime,
    apply: bool,
) -> PurgeOutcome:
    candidate = parse_pending_mark(label_prefix, image_ref, referrer.annotations)
    try:
        digest = registry.inspect(image_ref).digest
    except KnockError as exc:
        return PurgeOutcome(image_ref=image_ref, error=_err(exc))

    try:
        observation = oracle.last_prod_usage(
            UsageQuery(digest=digest, image_ref=image_ref, identity=candidate.identity, since=since)
        )
        decision = decide_purge(observation.last_seen, observed=True)
        reason = observation.detail
    except KnockError:
        decision = decide_purge(None, observed=False)  # fail-closed: oracle error => protect
        reason = "usage oracle unavailable"

    if decision is not PurgeDecision.purge:
        return PurgeOutcome(
            image_ref=image_ref, digest=digest, decision=decision.value, reason=reason
        )
    if not apply:
        return PurgeOutcome(image_ref=image_ref, digest=digest, decision="purge", reason=reason)
    try:
        # Clear the mark BEFORE removing the tag. Marks are discovered via the subject
        # tag (list_referrers on repo:tag), so deleting the tag first and then failing on
        # the referrer would strand an undiscoverable orphan mark until GC. Referrer-first
        # is self-healing: a failed tag-delete leaves a still-undesired tag that reconcile
        # re-marks next run.
        registry.delete_referrer(f"{repo_ref}@{referrer.digest}")
        registry.delete_tag(image_ref)
    except KnockError as exc:
        return PurgeOutcome(
            image_ref=image_ref, digest=digest, decision="purge", reason=reason, error=_err(exc)
        )
    return PurgeOutcome(
        image_ref=image_ref, digest=digest, decision="purge", reason=reason, applied=True
    )
