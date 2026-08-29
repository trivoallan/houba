"""Build the provenance stamp annotations for a mirrored/derived artifact (§9).

OCI-standard annotations carry the immutable build facts every scanner reads for
free; `{prefix}.*` carries knock facts (owners key, artifact type, three-level
policy.import.variant identity). No location fact is stamped — the same digest can
live in many registries.

Two shapes: `build_stamp_annotations` stamps an artifact mirrored/derived from an
upstream *image* and writes `base.name`/`base.digest`. `build_git_stamp_annotations`
stamps an artifact ingested from a *git* repository, which has no base image — ADR
0020 forbids fabricating one, so those keys are omitted and the upstream commit is
carried as `revision` instead.
"""

from __future__ import annotations

from datetime import datetime

from knock.errors import ConfigError, PolicyValidationError


def _oci_created_and_vendor(*, created: datetime, vendor: str | None) -> dict[str, str]:
    """The `image.created` (+ optional `image.vendor`) block, shared by every stamped artifact.

    vendor = the rebuilding org; org-specific so it is configuration, never hardcoded.
    """
    annotations: dict[str, str] = {"org.opencontainers.image.created": created.isoformat()}
    if vendor:
        annotations["org.opencontainers.image.vendor"] = vendor
    return annotations


def _lineage_annotations(
    *,
    prefix: str,
    owners: list[str] | None,
    artifact_type: str,
    policy: str,
    import_name: str,
    variant: str,
) -> dict[str, str]:
    """The `{prefix}.*` identity block, shared by every stamped artifact."""
    if not prefix:
        return {}
    annotations = {
        f"{prefix}.artifact.type": artifact_type,
        f"{prefix}.policy": policy,
        f"{prefix}.import": import_name,
        f"{prefix}.variant": variant,
    }
    if owners:
        annotations[f"{prefix}.owners"] = ",".join(owners)
    return annotations


def build_git_stamp_annotations(
    *,
    prefix: str,
    url: str,
    revision: str,
    title: str,
    created: datetime,
    owners: list[str] | None,
    vendor: str | None = None,
    artifact_type: str,
    policy: str,
    import_name: str,
    variant: str,
) -> dict[str, str]:
    """Stamp an artifact built from a git source rather than an upstream image.

    There is no base image, so `org.opencontainers.image.base.*` is omitted — ADR 0020
    forbids fabricating it. `revision` is the upstream commit, which is precisely what
    the OCI key means: the SCM revision of the packaged software.

    Requires a non-empty `prefix` (a `ConfigError`, like ADR 0041's `--require stamp`
    precedent): with no base image to anchor `is_stamped`'s empty-prefix fallback
    heuristic (`coverage.py`), the OCI-standard keys alone (title, source, revision,
    created) would be indistinguishable from an unstamped artifact, so this refuses
    rather than silently emit a stamp that can never read back as covered. This is
    configuration, not a policy fault — `KNOCK_LABEL_PREFIX` is an environment
    setting, the operator's `MirrorPolicy` is fine.

    Also refuses an empty `revision` (a `PolicyValidationError`: a data fault, not a
    config one) — that would be fabrication-by-emptiness, exactly what this function
    exists to avoid.
    """
    if not prefix:
        raise ConfigError(
            "stamping a git-sourced artifact requires a non-empty KNOCK_LABEL_PREFIX "
            "(default `io.knock`); a git source has no base image to anchor the "
            "empty-prefix coverage fallback"
        )
    if not revision:
        raise PolicyValidationError(
            "cannot stamp a git-sourced artifact with an empty revision: an empty "
            "org.opencontainers.image.revision is fabrication-by-emptiness"
        )
    annotations: dict[str, str] = {
        "org.opencontainers.image.title": title,
        "org.opencontainers.image.source": url,
        "org.opencontainers.image.revision": revision,
    }
    annotations.update(_oci_created_and_vendor(created=created, vendor=vendor))
    annotations.update(
        _lineage_annotations(
            prefix=prefix,
            owners=owners,
            artifact_type=artifact_type,
            policy=policy,
            import_name=import_name,
            variant=variant,
        )
    )
    return annotations


def build_stamp_annotations(
    *,
    prefix: str,
    source_registry: str,
    source_repository: str,
    source_tag: str,
    source_digest: str,
    source_revision: str | None,
    created: datetime,
    owners: list[str] | None,
    vendor: str | None = None,
    artifact_type: str,
    policy: str,
    import_name: str,
    variant: str,
    transform_steps: list[str] | None = None,
    transform_version_value: str | None = None,
) -> dict[str, str]:
    source = f"{source_registry}/{source_repository}"
    annotations: dict[str, str] = {
        # human-readable name = the upstream image's short name (so registry UIs read it for free)
        "org.opencontainers.image.title": source_repository.rsplit("/", 1)[-1],
        "org.opencontainers.image.source": source,
        "org.opencontainers.image.base.name": f"{source}:{source_tag}",
        "org.opencontainers.image.base.digest": source_digest,
    }
    annotations.update(_oci_created_and_vendor(created=created, vendor=vendor))
    # revision = the SCM revision of the *packaged software*, as the SOURCE image declares it
    # (OCI semantics). knock does not know the upstream commit, so it propagates the source's
    # own .revision when present and omits the key otherwise — never a fabricated digest/tag.
    if source_revision is not None:
        annotations["org.opencontainers.image.revision"] = source_revision
    annotations.update(
        _lineage_annotations(
            prefix=prefix,
            owners=owners,
            artifact_type=artifact_type,
            policy=policy,
            import_name=import_name,
            variant=variant,
        )
    )
    if prefix and transform_steps and transform_version_value is not None:
        annotations[f"{prefix}.transform.steps"] = ",".join(transform_steps)
        annotations[f"{prefix}.transform.version"] = transform_version_value
    return annotations
