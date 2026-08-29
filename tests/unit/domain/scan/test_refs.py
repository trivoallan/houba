from __future__ import annotations

from knock.domain.scan.refs import (
    is_referrers_fallback_tag,
    pin_to_digest,
    registry_host,
)

D = "sha256:newdigest"


def test_tag_ref_becomes_digest_pinned() -> None:
    assert pin_to_digest("harbor.corp/lib/redis:7.2.0", D) == f"harbor.corp/lib/redis@{D}"


def test_host_with_port_is_preserved() -> None:
    assert pin_to_digest("localhost:5000/lib/redis:7", D) == f"localhost:5000/lib/redis@{D}"


def test_existing_digest_is_replaced() -> None:
    assert pin_to_digest("harbor.corp/lib/redis@sha256:old", D) == f"harbor.corp/lib/redis@{D}"


def test_ref_without_tag_gets_digest_appended() -> None:
    assert pin_to_digest("harbor.corp/lib/redis", D) == f"harbor.corp/lib/redis@{D}"


def test_registry_host_with_dot() -> None:
    assert registry_host("harbor.corp/lib/redis:7.2.0") == "harbor.corp"


def test_registry_host_with_port() -> None:
    assert registry_host("localhost:5000/lib/redis:7") == "localhost:5000"


def test_registry_host_localhost_no_port() -> None:
    assert registry_host("localhost/lib/redis:7") == "localhost"


def test_registry_host_bare_name_is_none() -> None:
    assert registry_host("redis:7.2.0") is None


def test_registry_host_single_org_segment_is_none() -> None:
    assert registry_host("library/redis:7.2.0") is None


def test_registry_host_digest_pinned_ref() -> None:
    assert registry_host("harbor.corp/lib/redis@sha256:abc") == "harbor.corp"


_FALLBACK = "sha256-2f4da11ec2ed0fccf8e93186bf9bdd7b7115a649a0b954c1a09f776d5199174d"


def test_referrers_fallback_tag_is_recognised() -> None:
    assert is_referrers_fallback_tag(_FALLBACK) is True


def test_ordinary_tags_are_not_fallback_tags() -> None:
    for tag in ("1.38.0", "latest", "bookworm-slim-eu"):
        assert is_referrers_fallback_tag(tag) is False


def test_too_few_hex_digits_is_not_a_fallback_tag() -> None:
    assert is_referrers_fallback_tag("sha256-" + "a" * 63) is False


def test_too_many_hex_digits_is_not_a_fallback_tag() -> None:
    assert is_referrers_fallback_tag("sha256-" + "a" * 65) is False


def test_uppercase_hex_is_not_a_fallback_tag() -> None:
    assert is_referrers_fallback_tag("sha256-" + "A" * 64) is False


def test_other_digest_algorithms_are_not_recognised() -> None:
    assert is_referrers_fallback_tag("sha512-" + "a" * 64) is False


def test_merely_containing_the_prefix_is_not_a_fallback_tag() -> None:
    assert is_referrers_fallback_tag("v1-sha256-" + "a" * 64) is False
