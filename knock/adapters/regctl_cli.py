"""subprocess wrapper around regctl (OCI reads and writes)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from knock.errors import RegctlError
from knock.ports.registry import ImageInfo, Referrer


class RegctlAdapter:
    def __init__(self, binary: str | None = None) -> None:
        # Lazy resolution: only validate if an explicit binary is provided.
        # PATH resolution happens on the first call (lazy) so that constructing
        # the Container is not blocked in environments without regctl.
        if binary is not None:
            if not Path(binary).is_file():
                raise RegctlError(f"regctl binary not found: {binary}")
            self._bin: str | None = binary
        else:
            self._bin = None

    def _resolve(self) -> str:
        if self._bin is not None:
            return self._bin
        resolved = shutil.which("regctl")
        if not resolved:
            raise RegctlError("regctl binary not found in PATH")
        self._bin = resolved
        return self._bin

    def list_repositories(self, registry: str) -> list[str]:
        try:
            out = self._run(["repo", "ls", registry])
        except RegctlError as e:
            msg = str(e).lower()
            if "name_unknown" in msg or "not known to registry" in msg:
                return []
            raise
        return [line.strip() for line in out.splitlines() if line.strip()]

    def list_tags(self, repo_ref: str) -> list[str]:
        try:
            out = self._run(["tag", "ls", repo_ref])
        except RegctlError as e:
            # A never-pushed repo → dist-spec NAME_UNKNOWN; that means "no tags", not a
            # hard error (else the first reconcile of an empty mirror would always fail).
            msg = str(e).lower()
            if "name_unknown" in msg or "not known to registry" in msg:
                return []
            raise
        return [line.strip() for line in out.splitlines() if line.strip()]

    def inspect(self, image_ref: str) -> ImageInfo:
        digest = self._run(["image", "digest", image_ref]).strip()
        manifest = self._json(["manifest", "get", image_ref, "--format", "{{json .}}"])
        # `image config` on an index defaults to regctl's host platform, which fails when
        # the image lacks that arch (a single-platform rebuild read on a different-arch
        # node). Pin a platform the index actually carries.
        config_args = ["image", "config", image_ref, "--format", "{{json .}}"]
        platform = self._first_platform(manifest)
        if platform:
            config_args += ["--platform", platform]
        config = self._json(config_args)
        raw_annotations = manifest.get("annotations")
        annotations = dict(raw_annotations) if isinstance(raw_annotations, dict) else {}
        cfg = config.get("config")
        raw_labels = cfg.get("Labels") if isinstance(cfg, dict) else None
        config_labels = (
            {str(k): str(v) for k, v in raw_labels.items()} if isinstance(raw_labels, dict) else {}
        )
        created_raw = config.get("created")
        created = self._parse_time(created_raw) if isinstance(created_raw, str) else None
        return ImageInfo(
            digest=digest, created=created, annotations=annotations, config_labels=config_labels
        )

    def get_annotations(self, image_ref: str) -> tuple[str, dict[str, str]]:
        digest = self._run(["image", "digest", image_ref]).strip()
        manifest = self._json(["manifest", "get", image_ref, "--format", "{{json .}}"])
        raw = manifest.get("annotations")
        annotations = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        return digest, annotations

    def copy(self, src_ref: str, dst_ref: str) -> None:
        self._run(["image", "copy", src_ref, dst_ref])

    def annotate(
        self, image_ref: str, annotations: dict[str, str], *, publish_as: str | None = None
    ) -> str:
        args = ["image", "mod", image_ref]
        # --create publishes the annotated result at another ref, so image_ref can be
        # digest-pinned; --replace rewrites the tag in place when the caller has no digest.
        args += ["--create", publish_as] if publish_as else ["--replace"]
        for key, value in annotations.items():
            args += ["--annotation", f"{key}={value}"]
        self._run(args)
        # `image mod` prints the resulting reference, never a digest (and has no --format),
        # so the post-annotate digest still has to be read back from the published ref.
        return self._run(["image", "digest", publish_as or image_ref]).strip()

    def delete_tag(self, image_ref: str) -> None:
        self._run(["tag", "rm", image_ref])

    @staticmethod
    def _first_platform(manifest: dict[str, object]) -> str | None:
        """First concrete os/arch in an index manifest; None for a plain manifest.

        Skips the unknown/unknown attestation entries buildkit interleaves.
        """
        entries = manifest.get("manifests")
        if not isinstance(entries, list):
            return None
        for entry in entries:
            plat = entry.get("platform") if isinstance(entry, dict) else None
            if not isinstance(plat, dict):
                continue
            os_, arch = plat.get("os"), plat.get("architecture")
            if not os_ or not arch or "unknown" in (os_, arch):
                continue
            variant = plat.get("variant")
            return f"{os_}/{arch}/{variant}" if variant else f"{os_}/{arch}"
        return None

    def _parse_time(self, value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _json(self, args: list[str]) -> dict[str, object]:
        out = self._run(args)
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as e:
            raise RegctlError(f"invalid JSON from regctl {' '.join(args)}: {e}") from e
        if not isinstance(payload, dict):
            raise RegctlError(f"expected JSON object from regctl {' '.join(args)}: {payload!r}")
        return payload

    def configure_registry(self, host: str, *, tls_verify: bool, ca_cert: str | None) -> None:
        args = ["registry", "set", host, "--tls", "enabled" if tls_verify else "disabled"]
        if ca_cert:
            args += ["--cacert", ca_cert]
        self._run(args)

    def login(self, host: str, *, username: str, password: str, tls_verify: bool) -> None:
        args = ["registry", "login", "--user", username, "--pass-stdin"]
        if not tls_verify:
            args += ["--tls", "disabled"]
        args.append(host)
        self._run(args, stdin=password)

    def list_referrers(self, image_ref: str, artifact_type: str | None = None) -> list[Referrer]:
        args = ["artifact", "list", image_ref]
        if artifact_type is not None:
            args += ["--filter-artifact-type", artifact_type]
        args += ["--format", "{{json .}}"]
        payload = self._json(args)
        raw = payload.get("descriptors")
        descriptors = raw if isinstance(raw, list) else []
        out: list[Referrer] = []
        for d in descriptors:
            if not isinstance(d, dict):
                continue
            ann = d.get("annotations")
            out.append(
                Referrer(
                    digest=str(d.get("digest", "")),
                    artifact_type=str(d.get("artifactType", "")),
                    annotations=dict(ann) if isinstance(ann, dict) else {},
                    subject_tag=image_ref,
                )
            )
        return out

    def put_referrer(
        self,
        image_ref: str,
        artifact_type: str,
        annotations: dict[str, str],
        *,
        blob: bytes = b"",
        media_type: str | None = None,
    ) -> str:
        args = ["artifact", "put", "--subject", image_ref, "--artifact-type", artifact_type]
        for key, value in annotations.items():
            args += ["--annotation", f"{key}={value}"]
        if blob:
            with tempfile.NamedTemporaryFile("wb", suffix=".blob") as f:
                f.write(blob)
                f.flush()
                args += ["--file", f.name]
                if media_type:
                    args += ["--file-media-type", media_type]
                out = self._run(args)
        else:
            out = self._run(args, stdin="")
        return out.strip()

    def delete_referrer(self, referrer_ref: str) -> None:
        self._run(["manifest", "delete", referrer_ref])

    def _run(self, args: list[str], *, stdin: str | None = None) -> str:
        try:
            r = subprocess.run(  # noqa: S603
                [self._resolve(), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                input=stdin,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RegctlError(str(e)) from e
        if r.returncode != 0:
            raise RegctlError(f"regctl {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout
