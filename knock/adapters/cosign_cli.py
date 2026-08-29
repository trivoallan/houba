"""subprocess wrapper around cosign: signs an in-toto attestation (DSSE) and
attaches it as an OCI referrer to the subject digest.

Fail-fast like regctl/buildctl (CLAUDE.md: no retry logic in adapters —
cosign handles its own network retries internally). The trust model
(keyless | kms | key) is a *configuration* of the same port.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from knock.config import AttestSettings
from knock.domain.attestation import build_signing_config
from knock.errors import CosignError
from knock.ports.attestor import AttestationRef, VerifiedPredicate

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}(?![0-9a-f])")


class CosignAdapter:
    def __init__(self, config: AttestSettings, binary: str | None = None) -> None:
        self._config = config
        # Lazy resolution (cf. buildkit/regctl): does not block Container construction
        # in environments where cosign is not installed.
        if binary is not None:
            if not Path(binary).is_file():
                raise CosignError(f"cosign binary not found: {binary}")
            self._bin: str | None = binary
        else:
            self._bin = None

    def _resolve(self) -> str:
        if self._bin is not None:
            return self._bin
        resolved = shutil.which("cosign")
        if not resolved:
            raise CosignError("cosign binary not found in PATH")
        self._bin = resolved
        return self._bin

    def _key_args(self) -> list[str]:
        # A key reference is NOT a "service URL", so it stays a flag (kms/key).
        # keyless emits no --key: the identity comes from the signing-config / ambient OIDC.
        cfg = self._config
        if cfg.signer in ("kms", "key"):
            return ["--key", cfg.key_ref]
        return []

    def attest(self, subject_ref: str, statement: dict[str, Any]) -> AttestationRef:
        cfg = self._config
        predicate_type = str(statement.get("predicateType", ""))
        predicate = statement.get("predicate", {})
        signing_config = build_signing_config(
            fulcio_url=cfg.fulcio_url,
            rekor_url=cfg.rekor_url,
            operator=cfg.builder_id or "knock",
            keyless=cfg.signer == "keyless",
        )
        with tempfile.TemporaryDirectory(prefix="knock-attest-") as tmp:
            pred_path = Path(tmp) / "predicate.json"
            pred_path.write_text(json.dumps(predicate, sort_keys=True))
            scfg_path = Path(tmp) / "signing-config.json"
            scfg_path.write_text(json.dumps(signing_config, sort_keys=True))
            args = [
                "attest",
                "--yes",
                "--type",
                predicate_type,
                "--predicate",
                str(pred_path),
                *self._key_args(),
                "--signing-config",
                str(scfg_path),
                subject_ref,
            ]
            try:
                r = subprocess.run(  # noqa: S603
                    [self._resolve(), *args],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise CosignError(str(e)) from e
        if r.returncode != 0:
            raise CosignError(f"cosign attest failed: {r.stderr.strip()}")
        m = _DIGEST_RE.search(r.stderr) or _DIGEST_RE.search(r.stdout)
        return AttestationRef(
            predicate_type=predicate_type,
            referrer_digest=m.group(0) if m else "",
        )

    def _verify_args(self) -> list[str]:
        cfg = self._config
        if cfg.signer in ("kms", "key"):
            # Key/KMS signatures carry no Rekor entry -> skip the tlog check (kargo §9.1 #1).
            return ["--key", cfg.key_ref, "--insecure-ignore-tlog=true"]
        return [
            "--certificate-identity-regexp",
            cfg.verify_identity,
            "--certificate-oidc-issuer",
            cfg.verify_oidc_issuer,
        ]

    def verify(self, subject_ref: str, predicate_type: str) -> list[VerifiedPredicate]:
        args = ["verify-attestation", "--type", predicate_type, *self._verify_args(), subject_ref]
        try:
            r = subprocess.run(  # noqa: S603
                [self._resolve(), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise CosignError(str(e)) from e
        if r.returncode != 0:
            return []  # cosign ran, nothing verifiable -> fail-closed at the gate
        return _parse_verified_predicates(r.stdout)


def _parse_verified_predicates(stdout: str) -> list[VerifiedPredicate]:
    """Decode cosign verify-attestation DSSE lines to predicates; drop anything malformed."""
    out: list[VerifiedPredicate] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            bundle = json.loads(line)
            statement = json.loads(base64.b64decode(bundle["payload"]))
            pred = statement["predicate"]
            out.append(
                VerifiedPredicate(
                    summary=dict(pred["summary"]), attested_at=str(pred["attested_at"])
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, binascii.Error):
            continue
    return out
