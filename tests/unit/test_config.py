from pathlib import Path

import pytest
from pydantic import ValidationError

from knock.config import RegistryConfig, Settings, resolve_registry, settings_json_schema
from knock.errors import ConfigError


def test_settings_json_schema_carries_field_descriptions() -> None:
    # The generated config reference is only useful if descriptions live in the model;
    # guard that they reach the published schema (see scripts/gen_reference.py).
    schema = settings_json_schema()
    assert schema["properties"]["label_prefix"]["description"]
    assert schema["properties"]["max_concurrency"]["description"]


def test_settings_constructs_with_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings() doit fonctionner sans aucune variable d'environnement obligatoire."""
    monkeypatch.delenv("KNOCK_REGISTRIES", raising=False)
    s = Settings()
    assert s.registries == {}
    assert s.label_prefix == "io.knock"
    assert s.log_format == "text"
    assert s.log_level == "INFO"


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une valeur de `KNOCK_LOG_LEVEL` hors du Literal doit lever ValidationError."""
    monkeypatch.setenv("KNOCK_LOG_LEVEL", "TRACE")

    with pytest.raises(ValidationError):
        Settings()


def test_label_prefix_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """KNOCK_LABEL_PREFIX vaut 'io.knock' par défaut."""
    monkeypatch.delenv("KNOCK_LABEL_PREFIX", raising=False)
    assert Settings().label_prefix == "io.knock"


def test_label_prefix_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """KNOCK_LABEL_PREFIX peut être surchargé via la variable d'environnement."""
    monkeypatch.setenv("KNOCK_LABEL_PREFIX", "com.example.myorg")
    assert Settings().label_prefix == "com.example.myorg"


def test_work_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KNOCK_WORK_DIR", str(tmp_path))
    assert Settings().work_dir == tmp_path


def test_registry_config_minimal() -> None:
    r = RegistryConfig(host="harbor.corp.example.com")
    assert r.host == "harbor.corp.example.com"
    assert r.username is None
    assert r.password is None
    assert r.tls_verify is True  # default


def test_registry_config_with_credentials() -> None:
    r = RegistryConfig(host="h", username="robot", password="s3cret", tls_verify=False)
    assert r.username == "robot"
    assert r.password.get_secret_value() == "s3cret"
    assert r.tls_verify is False


def test_registry_config_password_masked_in_repr() -> None:
    r = RegistryConfig(host="h", username="u", password="leak-me")
    assert "leak-me" not in repr(r)


def test_registry_config_username_without_password_rejected() -> None:
    with pytest.raises(ValidationError, match="together"):
        RegistryConfig(host="h", username="robot")


def test_registry_config_password_without_username_rejected() -> None:
    with pytest.raises(ValidationError, match="together"):
        RegistryConfig(host="h", password="s3cret")


def test_registry_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        RegistryConfig(host="h", typpo="x")


def test_registries_roster_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCK_REGISTRIES", raising=False)
    assert Settings().registries == {}


def test_registries_roster_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "KNOCK_REGISTRIES",
        '{"eu": {"host": "harbor.eu.corp", "username": "robot", "password": "s3cret"},'
        ' "us": {"host": "harbor.us.corp"}}',
    )
    reg = Settings().registries
    assert set(reg) == {"eu", "us"}
    assert reg["eu"].host == "harbor.eu.corp"
    assert reg["eu"].password.get_secret_value() == "s3cret"
    assert reg["us"].username is None


def test_registries_roster_password_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "KNOCK_REGISTRIES",
        '{"eu": {"host": "h", "username": "u", "password": "roster-leak"}}',
    )
    assert "roster-leak" not in repr(Settings())


def test_registries_roster_invalid_entry_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # username without password → RegistryConfig validator fires during Settings parse
    monkeypatch.setenv("KNOCK_REGISTRIES", '{"eu": {"host": "h", "username": "robot"}}')
    with pytest.raises(ValidationError):
        Settings()


def test_resolve_registry_by_name() -> None:
    roster = {"eu": RegistryConfig(host="a"), "us": RegistryConfig(host="b")}
    name, cfg = resolve_registry("us", roster)
    assert name == "us"
    assert cfg.host == "b"


def test_resolve_registry_unknown_name_raises() -> None:
    with pytest.raises(ConfigError, match="unknown registry"):
        resolve_registry("zz", {"eu": RegistryConfig(host="a")})


def test_resolve_registry_omitted_with_single_uses_it() -> None:
    roster = {"only": RegistryConfig(host="a")}
    name, cfg = resolve_registry(None, roster)
    assert name == "only"
    assert cfg.host == "a"


def test_resolve_registry_omitted_with_multiple_raises() -> None:
    roster = {"eu": RegistryConfig(host="a"), "us": RegistryConfig(host="b")}
    with pytest.raises(ConfigError, match="specify one"):
        resolve_registry(None, roster)


def test_resolve_registry_omitted_with_empty_raises() -> None:
    with pytest.raises(ConfigError, match="no registries"):
        resolve_registry(None, {})


# ---------------------------------------------------------------------------
# Task 2 — CA cert sources, package mirrors, build_platform
# ---------------------------------------------------------------------------


from knock.config import (  # noqa: E402
    CACertSource,
    PackageMirror,
    resolve_ca_certs,
    resolve_mirror,
)


def test_ca_cert_source_accepts_path_only() -> None:
    assert CACertSource(path="/etc/knock/certs/corp.pem").path == "/etc/knock/certs/corp.pem"


def test_ca_cert_source_accepts_pem_only() -> None:
    assert CACertSource(pem="-----BEGIN CERTIFICATE-----\n...").pem is not None


def test_ca_cert_source_rejects_both_or_neither() -> None:
    with pytest.raises(ValueError, match="exactly one of path"):
        CACertSource(path="/x", pem="y")
    with pytest.raises(ValueError, match="exactly one of path"):
        CACertSource()


def test_package_mirror_requires_at_least_one_manager() -> None:
    assert PackageMirror(apt="https://mirror.corp").apt == "https://mirror.corp"
    with pytest.raises(ValueError, match="at least one of apt"):
        PackageMirror()


def test_rosters_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_TRANSFORM_CA_CERTS", '{"corp": {"path": "/etc/knock/certs/c.pem"}}')
    monkeypatch.setenv(
        "KNOCK_TRANSFORM_PACKAGE_MIRRORS",
        '{"corp": {"apt": "https://mirror.corp", "apk": "https://mirror.corp"}}',
    )
    s = Settings()
    assert s.transform_ca_certs["corp"].path == "/etc/knock/certs/c.pem"
    assert s.transform_package_mirrors["corp"].apt == "https://mirror.corp"


def test_build_platform_defaults_to_amd64() -> None:
    assert Settings().build_platform == "linux/amd64"


def test_resolve_ca_certs_returns_named_sources() -> None:
    roster = {"corp": CACertSource(pem="PEM")}
    assert resolve_ca_certs(["corp"], roster) == [("corp", CACertSource(pem="PEM"))]


def test_resolve_ca_certs_unknown_name_raises() -> None:
    with pytest.raises(ConfigError, match="unknown CA cert 'nope'"):
        resolve_ca_certs(["nope"], {})


def test_resolve_mirror_unknown_name_raises() -> None:
    with pytest.raises(ConfigError, match="unknown package mirror 'nope'"):
        resolve_mirror("nope", {})


# ---------------------------------------------------------------------------
# Task 1 — RegistryConfig.ca_cert
# ---------------------------------------------------------------------------


def test_registry_config_ca_cert_defaults_none() -> None:
    from knock.config import RegistryConfig

    assert RegistryConfig(host="harbor.corp").ca_cert is None


def test_registry_config_ca_cert_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "KNOCK_REGISTRIES",
        '{"corp": {"host": "harbor.corp", "tls_verify": false,'
        ' "ca_cert": "/etc/knock/registry-ca.pem"}}',
    )
    assert Settings().registries["corp"].ca_cert == "/etc/knock/registry-ca.pem"


# ---------------------------------------------------------------------------
# Task 3 — max_concurrency
# ---------------------------------------------------------------------------


def test_max_concurrency_defaults_to_4() -> None:
    from knock.config import Settings

    assert Settings().max_concurrency == 4


def test_max_concurrency_read_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from knock.config import Settings

    monkeypatch.setenv("KNOCK_MAX_CONCURRENCY", "8")
    assert Settings().max_concurrency == 8


def test_max_concurrency_rejects_zero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import pytest
    from pydantic import ValidationError

    from knock.config import Settings

    monkeypatch.setenv("KNOCK_MAX_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        Settings()


# ---------------------------------------------------------------------------
# Task 5 — KNOCK_DELETION_MODE global + RegistryConfig.deletion_mode
# ---------------------------------------------------------------------------


from knock.domain.deletion_mode import DeletionMode  # noqa: E402


def test_settings_deletion_mode_defaults_to_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCK_DELETION_MODE", raising=False)
    assert Settings().deletion_mode is DeletionMode.purge


def test_settings_deletion_mode_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KNOCK_DELETION_MODE", "mark")
    assert Settings().deletion_mode is DeletionMode.mark


def test_registry_config_deletion_mode_defaults_none() -> None:
    cfg = RegistryConfig(host="harbor.corp")
    assert cfg.deletion_mode is None


def test_registry_config_deletion_mode_parsed() -> None:
    cfg = RegistryConfig(host="harbor.corp", deletion_mode="mark")
    assert cfg.deletion_mode is DeletionMode.mark


# ---------------------------------------------------------------------------
# SLSA attestation config (KNOCK_ATTEST_*)
# ---------------------------------------------------------------------------


from knock.config import AttestSettings, attest_settings_json_schema  # noqa: E402


def test_attest_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("SIGNER", "KEY_REF", "FULCIO_URL", "REKOR_URL", "BUILDER_ID"):
        monkeypatch.delenv(f"KNOCK_ATTEST_{k}", raising=False)
    s = Settings()
    assert s.attest_signer == ""
    assert s.attest.signer == ""  # the typed DTO view


def test_attest_fields_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_ATTEST_SIGNER", "keyless")
    monkeypatch.setenv("KNOCK_ATTEST_FULCIO_URL", "https://fulcio.corp")
    monkeypatch.setenv("KNOCK_ATTEST_REKOR_URL", "https://rekor.corp")
    monkeypatch.setenv("KNOCK_ATTEST_BUILDER_ID", "https://knock.corp/builders/main")
    a = Settings().attest
    assert a.signer == "keyless"
    assert a.fulcio_url == "https://fulcio.corp"
    assert a.rekor_url == "https://rekor.corp"
    assert a.builder_id == "https://knock.corp/builders/main"


def test_attest_signer_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_ATTEST_SIGNER", "pgp")
    with pytest.raises(ValidationError):
        Settings()


def test_kms_signer_requires_key_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_ATTEST_SIGNER", "kms")
    monkeypatch.delenv("KNOCK_ATTEST_KEY_REF", raising=False)
    with pytest.raises(ValidationError, match="KEY_REF"):
        Settings()


def test_key_signer_with_key_ref_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_ATTEST_SIGNER", "key")
    monkeypatch.setenv("KNOCK_ATTEST_KEY_REF", "/keys/cosign.key")
    a = Settings().attest
    assert a.signer == "key"
    assert a.key_ref == "/keys/cosign.key"


def test_attest_settings_model_validates_key_requirement() -> None:
    with pytest.raises(ValueError, match="requires"):
        AttestSettings(signer="kms")
    assert AttestSettings(signer="kms", key_ref="awskms://k").key_ref == "awskms://k"


def test_attest_schema_is_serializable_and_lists_fields() -> None:
    import json

    schema = attest_settings_json_schema()
    json.dumps(schema)
    assert set(schema["properties"]) == {
        "signer",
        "key_ref",
        "fulcio_url",
        "rekor_url",
        "builder_id",
        "verify_identity",
        "verify_oidc_issuer",
    }


# ---------------------------------------------------------------------------
# Task 4 — KNOCK_RETENTION global retention tier
# ---------------------------------------------------------------------------


def test_settings_retention_defaults_to_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("KNOCK_RETENTION", raising=False)
    assert Settings().retention is None


def test_settings_retention_parses_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KNOCK_RETENTION", '{"keep": 5, "olderThanDays": 14}')
    s = Settings()
    assert s.retention is not None
    assert s.retention.keep == 5
    assert s.retention.older_than_days == 14


# ---------------------------------------------------------------------------
# match_registry_by_host resolver
# ---------------------------------------------------------------------------


from knock.config import match_registry_by_host  # noqa: E402


def _roster_for_host_match() -> dict[str, RegistryConfig]:
    return {
        "prod": RegistryConfig(host="harbor.corp"),
        "local": RegistryConfig(host="localhost:5000"),
    }


def test_match_registry_by_host_hits() -> None:
    name, cfg = match_registry_by_host("harbor.corp/lib/redis:7", _roster_for_host_match())
    assert name == "prod"
    assert cfg.host == "harbor.corp"


def test_match_registry_by_host_port_host() -> None:
    name, _cfg = match_registry_by_host("localhost:5000/lib/redis:7", _roster_for_host_match())
    assert name == "local"


def test_match_registry_by_host_no_match_returns_none() -> None:
    assert match_registry_by_host("other.example.com/x:1", _roster_for_host_match()) is None


def test_match_registry_by_host_no_host_returns_none() -> None:
    assert match_registry_by_host("redis:7", _roster_for_host_match()) is None


def test_match_registry_by_host_empty_roster_returns_none() -> None:
    assert match_registry_by_host("harbor.corp/x:1", {}) is None


def test_match_finds_a_path_prefixed_entry_from_a_ref_in_its_namespace() -> None:
    roster = {"ghcr": RegistryConfig(host="ghcr.io/acme")}
    match = match_registry_by_host("ghcr.io/acme/demo/redis:7.2.0", roster)
    assert match is not None and match[0] == "ghcr"


def test_match_rejects_a_ref_in_a_sibling_namespace() -> None:
    roster = {"ghcr": RegistryConfig(host="ghcr.io/acme")}
    assert match_registry_by_host("ghcr.io/acme-staging/demo/redis:7.2.0", roster) is None


def test_match_prefers_the_more_specific_entry_over_a_bare_one() -> None:
    roster = {
        "bare": RegistryConfig(host="ghcr.io"),
        "ns": RegistryConfig(host="ghcr.io/acme"),
    }
    match = match_registry_by_host("ghcr.io/acme/demo/redis:7.2.0", roster)
    assert match is not None and match[0] == "ns"


def test_match_falls_back_to_the_bare_entry_outside_any_namespace() -> None:
    roster = {
        "bare": RegistryConfig(host="ghcr.io"),
        "ns": RegistryConfig(host="ghcr.io/acme"),
    }
    match = match_registry_by_host("ghcr.io/other/demo/redis:7.2.0", roster)
    assert match is not None and match[0] == "bare"


# Nested namespaces (`artifactory.corp/docker-local` alongside
# `artifactory.corp/docker-local/team`) are a realistic roster shape: a ref inside the
# inner namespace matches BOTH entries by prefix, so only the longest-host-first sort
# separates them.


def test_match_prefers_the_inner_of_two_nested_namespaces() -> None:
    roster = {
        "outer": RegistryConfig(host="ghcr.io/acme"),
        "inner": RegistryConfig(host="ghcr.io/acme/team"),
    }
    match = match_registry_by_host("ghcr.io/acme/team/redis:7.2.0", roster)
    assert match is not None and match[0] == "inner"


def test_match_prefers_the_inner_namespace_whatever_the_roster_order() -> None:
    # Same roster, opposite insertion order: a regression to plain `roster.items()`
    # would pass the test above by dict-ordering luck and fail here.
    roster = {
        "inner": RegistryConfig(host="ghcr.io/acme/team"),
        "outer": RegistryConfig(host="ghcr.io/acme"),
    }
    match = match_registry_by_host("ghcr.io/acme/team/redis:7.2.0", roster)
    assert match is not None and match[0] == "inner"


def test_match_falls_back_to_the_outer_namespace_outside_the_inner_one() -> None:
    roster = {
        "outer": RegistryConfig(host="ghcr.io/acme"),
        "inner": RegistryConfig(host="ghcr.io/acme/team"),
    }
    match = match_registry_by_host("ghcr.io/acme/other/redis:7.2.0", roster)
    assert match is not None and match[0] == "outer"


# ---------------------------------------------------------------------------
# Task 9 — KNOCK_SCAN_REDIS config + loud flat-REDIS_* migration error
# ---------------------------------------------------------------------------


from knock.config import ScanRedisConfig, scan_redis_from_env  # noqa: E402, F401


def test_scan_redis_parsed_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (
        "REDIS_ADDR",
        "REDIS_WORK_STREAM",
        "REDIS_DEAD_STREAM",
        "REDIS_CONFIRMED_ZSET",
        "REDIS_PLACED_SET",
        "REDIS_GROUP",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("KNOCK_SCAN_REDIS", '{"addr": "r:6379", "group": "scan"}')
    cfg = scan_redis_from_env()
    assert cfg.addr == "r:6379" and cfg.group == "scan"


def test_scan_redis_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (
        "REDIS_ADDR",
        "REDIS_WORK_STREAM",
        "REDIS_DEAD_STREAM",
        "REDIS_CONFIRMED_ZSET",
        "REDIS_PLACED_SET",
        "REDIS_GROUP",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("KNOCK_SCAN_REDIS", raising=False)
    cfg = scan_redis_from_env()
    assert cfg.addr == "scan-queue-redis:6379" and cfg.work == "knock:scan:work"


def test_stale_flat_redis_var_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCK_SCAN_REDIS", '{"addr": "r:6379"}')
    monkeypatch.setenv("REDIS_ADDR", "old:6379")
    with pytest.raises(ConfigError, match="REDIS_ADDR set but ignored"):
        scan_redis_from_env()


def test_scan_redis_addr_without_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    for v in (
        "REDIS_ADDR",
        "REDIS_WORK_STREAM",
        "REDIS_DEAD_STREAM",
        "REDIS_CONFIRMED_ZSET",
        "REDIS_PLACED_SET",
        "REDIS_GROUP",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("KNOCK_SCAN_REDIS", '{"addr": "localhost"}')
    with pytest.raises(ValidationError, match="addr must be host:port"):
        scan_redis_from_env()


# ---------------------------------------------------------------------------
# RegistryConfig decomposes a path-prefixed host
# ---------------------------------------------------------------------------


def test_registry_host_strips_the_path_prefix() -> None:
    cfg = RegistryConfig(host="ghcr.io/acme")
    assert cfg.registry_host == "ghcr.io"
    assert cfg.path_prefix == "acme"


def test_a_bare_host_has_no_path_prefix() -> None:
    cfg = RegistryConfig(host="harbor.corp:443")
    assert cfg.registry_host == "harbor.corp:443"
    assert cfg.path_prefix == ""


def test_a_multi_segment_prefix_is_kept_whole() -> None:
    cfg = RegistryConfig(host="artifactory.corp/docker-local/team")
    assert cfg.registry_host == "artifactory.corp"
    assert cfg.path_prefix == "docker-local/team"


def test_host_rejects_a_scheme() -> None:
    with pytest.raises(ValidationError, match="must not carry a scheme"):
        RegistryConfig(host="https://ghcr.io/acme")


def test_host_rejects_a_trailing_slash() -> None:
    with pytest.raises(ValidationError, match="must not end with"):
        RegistryConfig(host="ghcr.io/acme/")


def test_host_rejects_a_leading_slash() -> None:
    with pytest.raises(ValidationError, match="must not start with"):
        RegistryConfig(host="/acme")


def test_host_rejects_an_empty_value() -> None:
    with pytest.raises(ValidationError):
        RegistryConfig(host="")


def test_host_rejects_a_whitespace_only_value() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        RegistryConfig(host="   ")


def test_host_rejects_embedded_whitespace() -> None:
    with pytest.raises(ValidationError, match="must not contain whitespace"):
        RegistryConfig(host=" ghcr.io/acme")


def test_host_rejects_an_empty_path_segment() -> None:
    with pytest.raises(ValidationError, match="empty path segment"):
        RegistryConfig(host="ghcr.io//acme")
