"""Shared registry-session setup: configure TLS/CA then log in, once per host.

reconcile, audit, and attach all need a registry authenticated and TLS-configured
before they touch it. This is the single place that block lives.
"""

from __future__ import annotations

from collections.abc import Iterator

from knock.config import RegistryConfig
from knock.ports.registry import RegistryPort


def ensure_registry_session(
    registry: RegistryPort, cfg: RegistryConfig, logged_in: set[str]
) -> None:
    """Configure and (if credentials are set) log into cfg's registry, at most once.

    Keyed on `registry_host`, not `host`: two roster entries sharing a registry under
    different namespaces (`ghcr.io/acme`, `ghcr.io/acme-staging`) are one session, and
    regctl's registry-level commands reject a host carrying a path.

    `logged_in` is the caller-owned set of registry hosts already set up; this function
    adds cfg's. Idempotent: a host already in the set is a no-op.
    """
    host = cfg.registry_host
    if host in logged_in:
        return
    registry.configure_registry(host, tls_verify=cfg.tls_verify, ca_cert=cfg.ca_cert)
    if cfg.username is not None and cfg.password is not None:
        registry.login(
            host,
            username=cfg.username,
            password=cfg.password.get_secret_value(),
            tls_verify=cfg.tls_verify,
        )
    logged_in.add(host)


def walk_repo_refs(registry: RegistryPort, cfg: RegistryConfig) -> Iterator[str]:
    """Yield every fully-qualified repo ref this roster entry owns.

    The catalog API is registry-wide, so a path-prefixed host (`ghcr.io/acme`) must filter
    the catalog down to its own namespace — otherwise a coverage walk reports on repos the
    entry does not own. A bare host filters nothing and yields the whole catalog, exactly
    as before.
    """
    prefix = cfg.path_prefix
    for repo in registry.list_repositories(cfg.registry_host):
        if prefix and not repo.startswith(f"{prefix}/"):
            continue
        yield f"{cfg.registry_host}/{repo}"
