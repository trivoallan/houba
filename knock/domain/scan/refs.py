"""Pure OCI-reference helpers: ref rewriting, host extraction, tag classification. No I/O."""

from __future__ import annotations

import re

# The OCI distribution spec's *referrers tag schema*: the fallback a registry without the
# referrers API uses to store a referrer manifest — tag `<algorithm>-<hex-digest>` derived
# from the subject's digest. Only sha256 is matched: it is the only algorithm knock has
# observed in the wild (GHCR, older Harbor/ECR), and a loose pattern risks hiding a real tag.
_REFERRERS_FALLBACK_TAG = re.compile(r"\Asha256-[0-9a-f]{64}\Z")


def is_referrers_fallback_tag(tag: str) -> bool:
    """True iff `tag` is a referrers-tag-schema fallback tag, not a real image tag.

    Registries lacking the referrers API (GHCR, older Harbor/ECR) push referrer manifests
    under such tags into the subject's own repository, so they surface in every `tag ls`.
    They are never images: inspecting one fails ("platform not found").
    """
    return _REFERRERS_FALLBACK_TAG.match(tag) is not None


def pin_to_digest(ref: str, digest: str) -> str:
    """Return `ref` rewritten to point at `digest` (drops any existing tag/digest).

    Handles a registry host with a port (the colon before the last `/` is not a tag
    separator) and an already-digest-pinned ref.
    """
    base = ref.split("@", 1)[0]
    slash = base.rfind("/")
    colon = base.rfind(":")
    if colon > slash:  # a `:tag` after the last path separator
        base = base[:colon]
    return f"{base}@{digest}"


def registry_host(ref: str) -> str | None:
    """Return the leading registry-host component of an OCI ref, or None when none.

    A ref's first path segment is a registry host only when it looks like one:
    it contains a '.' (DNS name), a ':' (host:port), or is exactly 'localhost'.
    Otherwise the leading segment is a namespace and the host is the implicit
    default registry (docker.io) — which the roster never holds, so we return
    None and the caller falls back to ambient config.
    """
    head = ref.split("/", 1)[0]
    if "/" not in ref:
        return None
    if "." in head or ":" in head or head == "localhost":
        return head
    return None
