from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knock.ports.registry import ImageInfo, RegistryPort
from tests.fakes.registry import FakeRegistryPort


def _info(digest: str) -> ImageInfo:
    return ImageInfo(digest=digest, created=datetime(2026, 1, 1, tzinfo=UTC), annotations={})


def test_fake_list_tags_seeded() -> None:
    fake = FakeRegistryPort(tags={"docker.io/redis": ["7.2.0", "7.3.0"]})
    assert fake.list_tags("docker.io/redis") == ["7.2.0", "7.3.0"]
    assert fake.list_tags("unknown") == []


def test_fake_inspect_seeded() -> None:
    fake = FakeRegistryPort(infos={"docker.io/redis:7.2.0": _info("sha256:a")})
    assert fake.inspect("docker.io/redis:7.2.0").digest == "sha256:a"


def test_fake_journals_writes() -> None:
    fake = FakeRegistryPort()
    fake.copy("src:1", "dst:1")
    fake.annotate("dst:1", {"org.opencontainers.image.base.digest": "sha256:a"})
    fake.delete_tag("dst:old")
    assert fake.copied == [("src:1", "dst:1")]
    assert fake.annotated == [("dst:1", {"org.opencontainers.image.base.digest": "sha256:a"})]
    assert fake.deleted == ["dst:old"]


def test_fake_inspect_unseeded_raises_clear_keyerror() -> None:
    with pytest.raises(KeyError, match="no seeded ImageInfo"):
        FakeRegistryPort().inspect("missing:1")


def test_fake_journals_login() -> None:
    fake = FakeRegistryPort()
    fake.login("harbor.corp", username="robot", password="s3cret", tls_verify=True)
    assert fake.logins == [("harbor.corp", "robot", True)]  # password NOT journalled


def test_fake_is_a_registry_port() -> None:
    fake: RegistryPort = FakeRegistryPort()
    assert fake is not None


def test_fake_journals_configure_registry() -> None:
    fake = FakeRegistryPort()
    fake.configure_registry("harbor.corp", tls_verify=False, ca_cert="/ca.pem")
    assert fake.configured == [("harbor.corp", False, "/ca.pem")]


def test_put_referrer_with_blob_journals_and_returns_blob_digest() -> None:
    reg = FakeRegistryPort()
    out = reg.put_referrer(
        "harbor.corp/lib/redis@sha256:abc",
        "application/vnd.knock.scan.result.v1",
        {"io.knock.scan.tool": "trivy"},
        blob=b"{}",
        media_type="application/sarif+json",
    )
    assert out.startswith("sha256:")
    assert reg.artifact_referrers == [
        (
            "harbor.corp/lib/redis@sha256:abc",
            "application/vnd.knock.scan.result.v1",
            "application/sarif+json",
            b"{}",
            {"io.knock.scan.tool": "trivy"},
        )
    ]


def test_put_artifact_journals_and_returns_blob_digest(tmp_path: Path) -> None:
    blob = tmp_path / "skill.zip"
    blob.write_bytes(b"PK\x03\x04")
    reg = FakeRegistryPort()
    out = reg.put_artifact(
        "registry.example/skills/probe:1.0.0",
        artifact_type="application/vnd.knock.skill.v1",
        blob_path=blob,
        media_type="application/zip",
        annotations={"org.opencontainers.image.revision": "deadbeef"},
    )
    assert out.startswith("sha256:")
    assert reg.artifacts == [
        (
            "registry.example/skills/probe:1.0.0",
            "application/vnd.knock.skill.v1",
            blob,
            "application/zip",
            {"org.opencontainers.image.revision": "deadbeef"},
        )
    ]
