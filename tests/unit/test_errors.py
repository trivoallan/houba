import pytest

from knock.errors import (
    AdapterError,
    ArtifactAnnotationError,
    ArtifactBlobPathError,
    BuildkitError,
    ConfigError,
    DomainError,
    InternalError,
    KnockError,
    PolicyValidationError,
    QueueError,
    QueueUnavailableError,
    RegctlError,
    ScanReportError,
    UnknownFormatError,
    exit_code_for,
)


def test_hierarchy() -> None:
    assert issubclass(DomainError, KnockError)
    assert issubclass(AdapterError, KnockError)
    assert issubclass(ConfigError, KnockError)
    assert issubclass(InternalError, KnockError)
    assert issubclass(RegctlError, AdapterError)
    assert issubclass(BuildkitError, AdapterError)


@pytest.mark.parametrize(
    "exc,expected_code",
    [
        (DomainError("x"), 1),
        (AdapterError("x"), 2),
        (RegctlError("x"), 2),
        (BuildkitError("x"), 2),
        (ConfigError("x"), 3),
        (InternalError("x"), 4),
    ],
)
def test_exit_codes(exc: KnockError, expected_code: int) -> None:
    assert exit_code_for(exc) == expected_code


def test_exit_code_for_unknown_exception() -> None:
    assert exit_code_for(RuntimeError("boom")) == 4


def test_policy_validation_error_is_domain_error_exit_1() -> None:
    err = PolicyValidationError("bad policy")
    assert isinstance(err, DomainError)
    assert exit_code_for(err) == 1


def test_cosign_error_is_adapter_exit_2() -> None:
    from knock.errors import CosignError, exit_code_for

    assert exit_code_for(CosignError("boom")) == 2


def test_scan_report_error_is_domain_exit_1() -> None:
    assert issubclass(ScanReportError, DomainError)
    assert exit_code_for(ScanReportError("bad sarif")) == 1


def test_unknown_format_error_is_domain_exit_1() -> None:
    assert issubclass(UnknownFormatError, DomainError)
    assert exit_code_for(UnknownFormatError("nope")) == 1


def test_queue_error_is_adapter_error_exit_2():
    assert issubclass(QueueError, AdapterError)
    assert exit_code_for(QueueError("boom")) == 2


def test_queue_unavailable_has_distinct_exit_5():
    assert issubclass(QueueUnavailableError, QueueError)
    assert exit_code_for(QueueUnavailableError("redis down")) == 5


def test_artifact_annotation_error_is_domain_exit_1():
    assert issubclass(ArtifactAnnotationError, DomainError)
    assert exit_code_for(ArtifactAnnotationError("bad key")) == 1


def test_artifact_blob_path_error_is_domain_exit_1():
    assert issubclass(ArtifactBlobPathError, DomainError)
    assert exit_code_for(ArtifactBlobPathError("not a file")) == 1
