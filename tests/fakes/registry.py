from __future__ import annotations

import hashlib
from pathlib import Path

from knock.errors import RegctlError
from knock.ports.registry import ImageInfo, Referrer


class FakeRegistryPort:
    def __init__(
        self,
        tags: dict[str, list[str]] | None = None,
        infos: dict[str, ImageInfo] | None = None,
        fail_copy: set[str] | None = None,
        fail_put: set[str] | None = None,
        fail_delete: set[str] | None = None,
        fail_inspect: set[str] | None = None,
        copy_barrier: object | None = None,  # threading.Barrier; typed loosely to avoid an import
        referrers: dict[str, list[Referrer]] | None = None,
        repositories: dict[str, list[str]] | None = None,
        annotations: dict[str, dict[str, str]] | None = None,
        fail_get: set[str] | None = None,
        digests: dict[str, str] | None = None,
    ) -> None:
        self._tags = tags or {}
        self._infos = infos or {}
        self._fail_copy = fail_copy or set()
        self._fail_put = fail_put or set()
        self._fail_delete = fail_delete or set()
        self._fail_inspect = fail_inspect or set()
        self._copy_barrier = copy_barrier
        self._referrers = referrers or {}
        self._repositories = repositories or {}
        self._annotations = annotations or {}
        self._fail_get = fail_get or set()
        self._digests = digests or {}
        self.copied: list[tuple[str, str]] = []
        self.annotated: list[tuple[str, dict[str, str]]] = []
        self.deleted: list[str] = []
        self.logins: list[tuple[str, str, bool]] = []
        self.configured: list[tuple[str, bool, str | None]] = []
        self.marked: list[tuple[str, str, dict[str, str]]] = []
        self.unmarked: list[str] = []
        self.artifact_referrers: list[tuple[str, str, str, bytes, dict[str, str]]] = []
        self.artifacts: list[tuple[str, str, Path, str, dict[str, str]]] = []

    def configure_registry(self, host: str, *, tls_verify: bool, ca_cert: str | None) -> None:
        self.configured.append((host, tls_verify, ca_cert))

    def list_repositories(self, registry: str) -> list[str]:
        return list(self._repositories.get(registry, []))

    def list_tags(self, repo_ref: str) -> list[str]:
        return list(self._tags.get(repo_ref, []))

    def inspect(self, image_ref: str) -> ImageInfo:
        if image_ref in self._fail_inspect:
            raise RegctlError(f"fake inspect failure for {image_ref}")
        try:
            return self._infos[image_ref]
        except KeyError:
            raise KeyError(f"FakeRegistryPort: no seeded ImageInfo for {image_ref!r}") from None

    def get_annotations(self, image_ref: str) -> tuple[str, dict[str, str]]:
        if image_ref in self._fail_get:
            raise RegctlError(f"fake get_annotations failure for {image_ref}")
        digest = self._digests.get(image_ref) or (
            f"sha256:{hashlib.sha256(image_ref.encode()).hexdigest()}"
        )
        return digest, dict(self._annotations.get(image_ref, {}))

    def copy(self, src_ref: str, dst_ref: str) -> None:
        if self._copy_barrier is not None:
            self._copy_barrier.wait()  # type: ignore[attr-defined]
        if dst_ref in self._fail_copy:
            raise RegctlError(f"fake copy failure for {dst_ref}")
        self.copied.append((src_ref, dst_ref))

    def annotate(
        self, image_ref: str, annotations: dict[str, str], *, publish_as: str | None = None
    ) -> str:
        self.annotated.append((image_ref, annotations))
        # deterministic synthetic post-annotate digest (distinct per ref); keyed on the
        # published ref because that is what the adapter reads the digest back from.
        return f"sha256:{hashlib.sha256((publish_as or image_ref).encode()).hexdigest()}"

    def delete_tag(self, image_ref: str) -> None:
        if image_ref in self._fail_delete:
            raise RegctlError(f"fake delete failure for {image_ref}")
        self.deleted.append(image_ref)

    def login(self, host: str, *, username: str, password: str, tls_verify: bool) -> None:
        self.logins.append((host, username, tls_verify))

    def list_referrers(self, image_ref: str, artifact_type: str | None = None) -> list[Referrer]:
        refs = self._referrers.get(image_ref, [])
        if artifact_type is None:
            return list(refs)
        return [r for r in refs if r.artifact_type == artifact_type]

    # Journals only; _referrers is a read-fixture seeded via the constructor (see list_referrers).
    def put_referrer(
        self,
        image_ref: str,
        artifact_type: str,
        annotations: dict[str, str],
        *,
        blob: bytes = b"",
        media_type: str | None = None,
    ) -> str:
        if image_ref in self._fail_put:
            raise RegctlError(f"fake put_referrer failure for {image_ref}")
        if blob:
            self.artifact_referrers.append(
                (image_ref, artifact_type, media_type, blob, annotations)
            )
        else:
            self.marked.append((image_ref, artifact_type, annotations))
        return f"sha256:{hashlib.sha256(blob).hexdigest()}"

    def delete_referrer(self, referrer_ref: str) -> None:
        self.unmarked.append(referrer_ref)

    def put_artifact(
        self,
        image_ref: str,
        *,
        artifact_type: str,
        blob_path: Path,
        media_type: str,
        annotations: dict[str, str],
    ) -> str:
        if image_ref in self._fail_put:
            raise RegctlError(f"fake put_artifact failure for {image_ref}")
        self.artifacts.append((image_ref, artifact_type, blob_path, media_type, dict(annotations)))
        # deterministic synthetic manifest digest, keyed on the path string (not its
        # contents — the fake shouldn't need a real file on disk to seed a test). This is
        # a *manifest* digest, like the real adapter returns, not a layer digest — those
        # are never the same thing.
        return f"sha256:{hashlib.sha256(str(blob_path).encode()).hexdigest()}"
