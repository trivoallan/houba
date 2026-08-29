from __future__ import annotations

from knock.config import RegistryConfig
from knock.use_cases.registry_session import ensure_registry_session
from tests.fakes.registry import FakeRegistryPort


def test_configures_and_logs_in_with_credentials() -> None:
    reg = FakeRegistryPort()
    cfg = RegistryConfig(
        host="harbor.corp", username="u", password="p", tls_verify=False, ca_cert="/ca.pem"
    )
    logged_in: set[str] = set()
    ensure_registry_session(reg, cfg, logged_in)
    assert reg.configured == [("harbor.corp", False, "/ca.pem")]
    assert reg.logins == [("harbor.corp", "u", False)]
    assert logged_in == {"harbor.corp"}


def test_skips_login_without_credentials() -> None:
    reg = FakeRegistryPort()
    cfg = RegistryConfig(host="harbor.corp")
    ensure_registry_session(reg, cfg, set())
    assert reg.configured == [("harbor.corp", True, None)]
    assert reg.logins == []


def test_idempotent_per_host() -> None:
    reg = FakeRegistryPort()
    cfg = RegistryConfig(host="harbor.corp", username="u", password="p")
    logged_in: set[str] = set()
    ensure_registry_session(reg, cfg, logged_in)
    ensure_registry_session(reg, cfg, logged_in)
    assert len(reg.configured) == 1
    assert len(reg.logins) == 1


def test_logs_in_with_the_bare_host_when_the_entry_is_path_prefixed() -> None:
    reg = FakeRegistryPort()
    cfg = RegistryConfig(host="ghcr.io/acme", username="u", password="p")
    logged_in: set[str] = set()
    ensure_registry_session(reg, cfg, logged_in)
    assert reg.configured == [("ghcr.io", True, None)]
    assert reg.logins == [("ghcr.io", "u", True)]
    assert logged_in == {"ghcr.io"}


def test_two_namespaces_on_one_registry_share_a_single_session() -> None:
    reg = FakeRegistryPort()
    logged_in: set[str] = set()
    ensure_registry_session(
        reg, RegistryConfig(host="ghcr.io/acme", username="u", password="p"), logged_in
    )
    ensure_registry_session(
        reg, RegistryConfig(host="ghcr.io/acme-staging", username="u", password="p"), logged_in
    )
    assert len(reg.logins) == 1
