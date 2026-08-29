"""Turning a `RegistryConfig` roster entry into usable registry access.

Every use case that walks a registry needs the same two things first: a session —
TLS/CA configured and, where credentials exist, logged in — and the set of
repositories the entry actually owns, which for a path-prefixed host is a subset of
the registry-wide catalog. Both live here so no caller reinvents either.
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

    Assumes `ensure_registry_session` has already run for this entry: the catalog read
    goes to an authenticated registry, and this function does not establish the session
    itself.

    The catalog API is registry-wide, so a path-prefixed host (`ghcr.io/acme`) must filter
    the catalog down to its own namespace — otherwise a coverage walk reports on repos the
    entry does not own. The match requires a trailing slash, so a sibling namespace
    (`acme-staging`) is excluded, and so is a repository named exactly the prefix: knock
    composes destinations as `{host}/{project}/{repository}` and never places an image at
    a bare namespace, so such an entry is someone else's repo colliding with the name. A
    bare host filters nothing and yields the whole catalog, exactly as before.
    """
    host = cfg.registry_host
    prefix = cfg.path_prefix
    for repo in registry.list_repositories(host):
        if prefix and not repo.startswith(f"{prefix}/"):
            continue
        yield f"{host}/{repo}"
