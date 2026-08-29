"""Coverage audit (roadmap ④): catalog-walk the registry and report images that do NOT
carry knock's provenance stamp. Read-only; depends only on RegistryPort. Sequential v1,
structurally a sibling of `use_cases/purge.py`.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from knock.config import RegistryConfig, resolve_registry
from knock.domain.attestation import COSIGN_ATTESTATION_ARTIFACT_TYPE
from knock.domain.coverage import is_stamped
from knock.domain.sbom import FORMAT_MEDIA_TYPES
from knock.errors import KnockError, exit_code_for
from knock.ports.registry import RegistryPort
from knock.ports.reporter import ErrorInfo
from knock.use_cases.registry_session import ensure_registry_session, walk_repo_refs


class CoverageOutcome(BaseModel):
    image_ref: str
    digest: str | None = None  # manifest digest — the portal's stable join key; None on read error
    covered: bool = False
    signed: bool | None = None  # None = not probed; set only for covered images when check_signed
    sbom: bool | None = None  # None = not probed; set only for covered images when check_sbom
    policy: str | None = None  # {prefix}.policy when covered & present (audit context)
    error: ErrorInfo | None = None  # set => a hard failure reading this image


class CoverageCounts(BaseModel):
    scanned: int = 0
    covered: int = 0
    uncovered: int = 0
    signed: int = 0
    unsigned: int = 0
    with_sbom: int = 0
    without_sbom: int = 0
    errored: int = 0


class CoverageReport(BaseModel):
    registries: list[str]
    counts: CoverageCounts
    outcomes: list[CoverageOutcome]


def coverage_report_json_schema() -> dict[str, Any]:
    """Published JSON Schema for the coverage report (derived, never hand-written)."""
    return CoverageReport.model_json_schema()


def audit_exit_code(
    report: CoverageReport, *, fail_on_uncovered: bool, fail_on_unsigned: bool = False
) -> int:
    """Worst per-image read-error code if any; else 1 when a gating tier is non-empty; else 0."""
    codes = [o.error.exit_code for o in report.outcomes if o.error is not None]
    if codes:
        return max(codes)
    if fail_on_uncovered and report.counts.uncovered > 0:
        return 1
    if fail_on_unsigned and report.counts.unsigned > 0:
        return 1
    return 0


def _err(exc: BaseException) -> ErrorInfo:
    return ErrorInfo(type=type(exc).__name__, message=str(exc), exit_code=exit_code_for(exc))


def _classify(
    image_ref: str,
    *,
    registry: RegistryPort,
    label_prefix: str,
    check_signed: bool,
    check_sbom: bool,
) -> CoverageOutcome:
    try:
        digest, annotations = registry.get_annotations(image_ref)
        covered = is_stamped(annotations, prefix=label_prefix)
        policy = annotations.get(f"{label_prefix}.policy") if (covered and label_prefix) else None
        signed: bool | None = None
        if check_signed and covered:
            signed = bool(registry.list_referrers(image_ref, COSIGN_ATTESTATION_ARTIFACT_TYPE))
        sbom: bool | None = None
        if check_sbom and covered:
            sbom = any(registry.list_referrers(image_ref, mt) for mt in FORMAT_MEDIA_TYPES.values())
        return CoverageOutcome(
            image_ref=image_ref,
            digest=digest,
            covered=covered,
            signed=signed,
            sbom=sbom,
            policy=policy,
        )
    except KnockError as exc:
        return CoverageOutcome(image_ref=image_ref, error=_err(exc))


def audit_coverage(
    *,
    registry: RegistryPort,
    roster: dict[str, RegistryConfig],
    only_registry: str | None,
    label_prefix: str,
    check_signed: bool = False,
    check_sbom: bool = False,
    limit: int | None = None,
) -> CoverageReport:
    if only_registry is not None:
        name, cfg = resolve_registry(only_registry, roster)
        targets = [(name, cfg)]
    else:
        targets = list(roster.items())

    logged_in: set[str] = set()

    def _image_refs() -> Iterator[str]:
        for _name, cfg in targets:
            ensure_registry_session(registry, cfg, logged_in)
            for repo_ref in walk_repo_refs(registry, cfg):
                for tag in registry.list_tags(repo_ref):
                    yield f"{repo_ref}:{tag}"

    refs: Iterator[str] = _image_refs()
    if limit is not None:
        refs = itertools.islice(refs, limit)

    outcomes: list[CoverageOutcome] = [
        _classify(
            ref,
            registry=registry,
            label_prefix=label_prefix,
            check_signed=check_signed,
            check_sbom=check_sbom,
        )
        for ref in refs
    ]

    counts = CoverageCounts(
        scanned=len(outcomes),
        covered=sum(1 for o in outcomes if o.error is None and o.covered),
        uncovered=sum(1 for o in outcomes if o.error is None and not o.covered),
        signed=sum(1 for o in outcomes if o.signed is True),
        unsigned=sum(1 for o in outcomes if o.signed is False),
        with_sbom=sum(1 for o in outcomes if o.sbom is True),
        without_sbom=sum(1 for o in outcomes if o.sbom is False),
        errored=sum(1 for o in outcomes if o.error is not None),
    )
    return CoverageReport(
        registries=[cfg.host for _name, cfg in targets], counts=counts, outcomes=outcomes
    )
