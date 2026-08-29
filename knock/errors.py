"""Exception hierarchy and exit-code table for the knock CLI.

See spec §6.3.
"""

from __future__ import annotations

__all__ = [
    "AdapterError",
    "ArchiveError",
    "ArchiveLayoutError",
    "ArtifactAnnotationError",
    "ArtifactBlobPathError",
    "BuildkitError",
    "ConfigError",
    "CosignError",
    "DomainError",
    "InternalError",
    "KnockError",
    "PolicyValidationError",
    "QueueError",
    "QueueUnavailableError",
    "RegctlError",
    "ScanReportError",
    "SyftError",
    "UnknownFormatError",
    "UnsupportedSourceError",
    "UsageOracleError",
    "exit_code_for",
]


class KnockError(Exception):
    """Root of all domain/infrastructure errors raised by the CLI."""


class DomainError(KnockError):
    """Business logic or validation error (exit 1)."""


class ArchiveError(DomainError):
    """A source tree cannot be packaged safely (symlink, path escape, collision, or
    size)."""


class ArchiveLayoutError(ArchiveError):
    """A tree passed every safety check but carries no recognised plugin marker at its
    root. Layout, not safety — kept as its own subclass (still exit 1 via DomainError in
    its MRO) so that a future policy escape hatch for the layout check could never also
    let a symlink, traversal, collision, or size refusal be bypassed."""


class ArtifactAnnotationError(DomainError):
    """A `put_artifact` annotation key is empty or contains '=' — regctl splits each
    --annotation token on the first '=', so either would silently mangle the pushed
    annotation (RC 0) rather than fail loudly."""


class ArtifactBlobPathError(DomainError):
    """A `put_artifact` blob_path does not exist or is not a regular file (e.g. a
    directory, which regctl otherwise pushes as a bogus layer at RC 0)."""


class PolicyValidationError(DomainError):
    """`MirrorPolicy` YAML invalid (schema, unknown field, inconsistent spec)."""


class ScanReportError(DomainError):
    """Scan report is unparseable, has an unexpected schema, or its subject digest mismatches."""


class UnknownFormatError(DomainError):
    """The scan report format could not be detected and no valid --format was supplied."""


class UnsupportedSourceError(DomainError):
    """A policy's source is a valid MirrorPolicy shape, but this use case does not
    handle that source kind yet (e.g. a git source reaching a registry-only reconcile
    run)."""


class AdapterError(KnockError):
    """Infrastructure / external-dependency error (exit 2)."""


class RegctlError(AdapterError):
    """`regctl` invocation error (tag ls, inspect, copy, mod, rm)."""


class BuildkitError(AdapterError):
    """`buildctl` invocation error (image build and push)."""


class CosignError(AdapterError):
    """`cosign` invocation error (attest / sign DSSE attestations)."""


class SyftError(AdapterError):
    """`syft` invocation error (SBOM generation on copy and rebuild paths)."""


class UsageOracleError(AdapterError):
    """Usage-oracle invocation error (external command unreachable or invalid output)."""


class QueueError(AdapterError):
    """Scan-queue adapter error (Redis Streams reserve/ack/reap/dlq)."""


class QueueUnavailableError(QueueError):
    """The scan queue (Redis) is unreachable — a distinct exit code so the
    pod-restart alert can tell a benign broker flap from a real failure storm."""


class ConfigError(KnockError):
    """Invalid or missing configuration (exit 3)."""


class InternalError(KnockError):
    """Bug, failed assertion, or unexpected condition (exit 4)."""


# Keys must be the root of each branch (siblings with no inheritance relationship).
# `exit_code_for` walks the exception's MRO and takes the first match — the ordering
# of entries in this dict does not matter as long as the keys remain branch roots.
_EXIT_CODES: dict[type[KnockError], int] = {
    DomainError: 1,
    AdapterError: 2,
    ConfigError: 3,
    InternalError: 4,
    QueueUnavailableError: 5,
}


def exit_code_for(exc: BaseException) -> int:
    """Return the exit code for an exception by walking its MRO.

    Any exception not rooted in `KnockError` (e.g. `RuntimeError`, `KeyError`)
    is treated as an `InternalError` (exit 4).
    """
    for klass in type(exc).__mro__:
        code = _EXIT_CODES.get(klass)
        if code is not None:
            return code
    return 4
