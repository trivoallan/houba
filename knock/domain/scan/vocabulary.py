"""The published `io.knock.scan.*` annotation-key vocabulary, derived from the registry.

This is the scan analogue of the policy JSON Schema: editors/CI and downstream consumers
read it to know which keys a scan referrer carries. Never hand-write the key list.
"""

from __future__ import annotations

from knock.domain.scan.formats.registry import DEFAULT_REGISTRY, Registry

COMMON_KEYS = ["scan.tool", "scan.format", "scan.timestamp", "scan.subject"]
OPTIONAL_KEYS = ["scan.tool.version", "scan.ruleset.hash"]


def scan_annotation_vocabulary(registry: Registry = DEFAULT_REGISTRY) -> dict[str, object]:
    """The common envelope keys plus each format's fact keys (prefixed with `scan.`)."""
    facts = {mapper.name: [f"scan.{k}" for k in mapper.fact_keys] for mapper in registry.mappers()}
    return {"common": list(COMMON_KEYS), "optional": list(OPTIONAL_KEYS), "facts": facts}
