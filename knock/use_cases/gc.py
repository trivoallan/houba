"""`knock gc`: collect superseded scan-result referrers across the roster.

Twin of purge's catalog walk, minus the usage oracle — the keep/age decision is
pure (domain/scan/gc). The walk is SEQUENTIAL in v1. Partial-failure: a
per-subject hard error is recorded and reddens the exit; it never blocks siblings.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from knock.config import RegistryConfig, resolve_registry
from knock.domain.scan.constants import SCAN_RESULT_ARTIFACT_TYPE
from knock.domain.scan.gc import select_superseded_referrers
from knock.domain.scan.refs import is_referrers_fallback_tag
from knock.errors import KnockError, exit_code_for
from knock.ports.registry import RegistryPort
from knock.ports.reporter import ErrorInfo
from knock.use_cases.registry_session import ensure_registry_session, walk_repo_refs

GcMode = Literal["apply", "dry-run"]


class GcOutcome(BaseModel):
    image_ref: str
    kept: int = 0
    collected: list[str] = Field(default_factory=list)
    applied: bool = False
    error: ErrorInfo | None = None  # set => a hard failure processing this subject


class GcReport(BaseModel):
    mode: GcMode
    outcomes: list[GcOutcome]


def gc_exit_code(report: GcReport) -> int:
    """0 unless a subject hit a hard error; then the worst failure exit code."""
    codes = [o.error.exit_code for o in report.outcomes if o.error is not None]
    return max(codes) if codes else 0


def _err(exc: BaseException) -> ErrorInfo:
    return ErrorInfo(type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc))


def gc_referrers(
    *,
    registry: RegistryPort,
    roster: dict[str, RegistryConfig],
    only_registry: str | None,
    label_prefix: str,
    keep: int,
    older_than_days: int,
    now: datetime,
    apply: bool,
) -> GcReport:
    mode: GcMode = "apply" if apply else "dry-run"
    older_than = timedelta(days=older_than_days)

    if only_registry is not None:
        name, cfg = resolve_registry(only_registry, roster)
        targets = [(name, cfg)]
    else:
        targets = list(roster.items())

    outcomes: list[GcOutcome] = []
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
                outcomes.append(
                    _process(
                        repo_ref=repo_ref,
                        image_ref=image_ref,
                        registry=registry,
                        label_prefix=label_prefix,
                        keep=keep,
                        older_than=older_than,
                        now=now,
                        apply=apply,
                    )
                )
    return GcReport(mode=mode, outcomes=outcomes)


def _process(
    *,
    repo_ref: str,
    image_ref: str,
    registry: RegistryPort,
    label_prefix: str,
    keep: int,
    older_than: timedelta,
    now: datetime,
    apply: bool,
) -> GcOutcome:
    try:
        referrers = registry.list_referrers(image_ref, SCAN_RESULT_ARTIFACT_TYPE)
    except KnockError as exc:
        return GcOutcome(image_ref=image_ref, error=_err(exc))

    collected = select_superseded_referrers(
        referrers, keep=keep, older_than=older_than, now=now, prefix=label_prefix
    )
    kept = len(referrers) - len(collected)
    if not collected or not apply:
        return GcOutcome(image_ref=image_ref, kept=kept, collected=collected)
    try:
        for digest in collected:
            registry.delete_referrer(f"{repo_ref}@{digest}")
    except KnockError as exc:
        return GcOutcome(image_ref=image_ref, kept=kept, collected=collected, error=_err(exc))
    return GcOutcome(image_ref=image_ref, kept=kept, collected=collected, applied=True)
