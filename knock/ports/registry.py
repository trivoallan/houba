"""Unified OCI registry access port (via regctl): reads and writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ImageInfo:
    digest: str  # manifest/index digest of the ref
    created: datetime | None  # image build time (proxy for source freshness)
    annotations: dict[str, str]  # OCI annotations (incl. recorded base.digest on mirror)
    # image-config Labels (e.g. upstream org.opencontainers.image.revision)
    config_labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Referrer:
    digest: str  # the referrer manifest digest
    artifact_type: str
    annotations: dict[str, str]
    subject_tag: str  # the output-tag whose manifest it refers to


class RegistryPort(Protocol):
    def configure_registry(self, host: str, *, tls_verify: bool, ca_cert: str | None) -> None: ...
    def list_repositories(self, registry: str) -> list[str]: ...
    def list_tags(self, repo_ref: str) -> list[str]: ...
    def inspect(self, image_ref: str) -> ImageInfo: ...
    def get_annotations(self, image_ref: str) -> tuple[str, dict[str, str]]:
        """Return (manifest digest, OCI annotations) for a ref — two reads (digest + manifest),
        no config blob.

        Cheaper than `inspect` (skips the image-config fetch) — for whole-registry coverage
        sweeps that also need the digest as a stable, replication-surviving join key.
        """
        ...

    def copy(self, src_ref: str, dst_ref: str) -> None: ...
    def annotate(
        self, image_ref: str, annotations: dict[str, str], *, publish_as: str | None = None
    ) -> str:
        """Annotate image_ref; publish the result to publish_as (default: in place).

        Returns the resulting (post-annotate) manifest digest. A caller that already knows
        the digest it placed should pass a digest-pinned image_ref plus the tag to publish,
        so the stamp cannot land on bytes a concurrent writer moved the tag to.
        """
        ...

    def delete_tag(self, image_ref: str) -> None: ...
    def login(self, host: str, *, username: str, password: str, tls_verify: bool) -> None: ...
    def list_referrers(
        self, image_ref: str, artifact_type: str | None = None
    ) -> list[Referrer]: ...
    def put_referrer(
        self,
        image_ref: str,
        artifact_type: str,
        annotations: dict[str, str],
        *,
        blob: bytes = b"",
        media_type: str | None = None,
    ) -> str:
        """Attach a referrer to image_ref.

        No blob → annotation-only marker (e.g. soft-delete); with a blob → an artifact referrer
        carrying media_type. Returns the referrer manifest digest.
        """
        ...

    def delete_referrer(self, referrer_ref: str) -> None: ...

    def put_artifact(
        self,
        image_ref: str,
        *,
        artifact_type: str,
        blob_path: Path,
        media_type: str,
        annotations: dict[str, str],
    ) -> str:
        """Push a standalone artifact whose single layer is the file at `blob_path`.

        Distinct from `put_referrer`, which always hangs off a subject. Returns the
        resulting manifest digest. The layer digest is the sha256 of the file itself,
        which is what a content-addressed consumer will pin.

        Takes a path rather than `put_referrer`'s in-memory `bytes`: regctl streams the
        file straight to the registry, so a large bundle (e.g. a skill zip) is never
        materialised in the Python process.
        """
        ...
