from datetime import datetime
from pathlib import Path

import pytest

from knock.adapters.regctl_cli import RegctlAdapter
from knock.errors import RegctlError


def test_list_tags(fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "tags-redis")
    assert RegctlAdapter().list_tags("docker.io/redis") == ["7.2.0", "7.3.0", "latest"]


def test_list_tags_empty(fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "empty")
    assert RegctlAdapter().list_tags("docker.io/redis") == []


def test_list_tags_repo_not_found_returns_empty(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A never-pushed destination repo → regctl exits non-zero with NAME_UNKNOWN.
    # That means "no tags", not a hard error — else the very first reconcile (empty
    # mirror) would always fail. Surfaced by real-registry testing.
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "notfound")
    assert RegctlAdapter().list_tags("localhost:5001/demo/absent") == []


def test_inspect_digest_created_annotations(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "mirror-stamped")
    info = RegctlAdapter().inspect("harbor.corp/lib/redis:7.2.0")
    assert info.digest == "sha256:abc123"
    assert info.created == datetime.fromisoformat("2026-01-02T03:04:05+00:00")
    assert info.annotations["org.opencontainers.image.base.digest"] == "sha256:src999"


def test_inspect_no_annotations(fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    info = RegctlAdapter().inspect("harbor.corp/lib/redis:7.2.0")
    assert info.annotations == {}


def test_inspect_index_pins_concrete_platform(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `image config` on an index defaults to the host's platform; that fails when the
    # image lacks it (a single-platform rebuild read on a different-arch node). inspect
    # must pin a platform the index actually has — skipping the unknown/unknown
    # attestation entry. Surfaced by `make local` on arm64 reading an amd64 rebuild.
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "index-amd64")
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().inspect("registry/demo/debian:bookworm-slim-eu")
    text = log.read_text()
    assert "image config" in text
    assert "--platform linux/amd64" in text


def test_inspect_plain_manifest_omits_platform_flag(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A plain (non-index) manifest has no platform list; regctl reads its single config
    # directly, so inspect must not invent a --platform that doesn't apply.
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().inspect("harbor.corp/lib/redis:7.2.0")
    config_line = next(ln for ln in log.read_text().splitlines() if ln.startswith("image config"))
    assert "--platform" not in config_line


def test_read_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().list_tags("docker.io/redis")


def test_garbage_json_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "garbage")
    with pytest.raises(RegctlError, match="JSON"):
        RegctlAdapter().inspect("harbor.corp/lib/redis:7.2.0")


def test_explicit_missing_binary_raises() -> None:
    with pytest.raises(RegctlError, match="not found"):
        RegctlAdapter(binary="/nonexistent/regctl")


def _log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "regctl.log"
    monkeypatch.setenv("FAKE_REGCTL_LOG", str(log))
    return log


def test_copy_invokes_image_copy(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().copy("docker.io/redis:7.2.0", "harbor.corp/lib/redis:7.2.0")
    assert "image copy docker.io/redis:7.2.0 harbor.corp/lib/redis:7.2.0" in log.read_text()


def test_annotate_emits_one_flag_per_annotation(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    result = RegctlAdapter().annotate(
        "harbor.corp/lib/redis:7.2.0",
        {"org.opencontainers.image.base.digest": "sha256:src", "io.knock.lineage": "copy"},
    )
    line = log.read_text()
    assert "image mod" in line
    assert "--annotation org.opencontainers.image.base.digest=sha256:src" in line
    assert "--annotation io.knock.lineage=copy" in line
    # annotate returns the resulting (post-mod) manifest digest, read back via `image digest`
    assert result == "sha256:abc123"
    assert "image digest harbor.corp/lib/redis:7.2.0" in line
    assert "--replace" in line  # no publish_as → rewrite the tag in place


def test_annotate_publishes_to_another_ref_when_input_is_pinned(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A digest-pinned input has no tag for --replace to rewrite, so the annotated result
    # is published to the destination tag with --create. This is what lets the copy path
    # stamp the digest it placed instead of re-resolving a tag a concurrent writer can move.
    log = _log(tmp_path, monkeypatch)
    result = RegctlAdapter().annotate(
        "harbor.corp/lib/redis@sha256:src",
        {"io.knock.policy": "redis"},
        publish_as="harbor.corp/lib/redis:7.2.0",
    )
    line = log.read_text()
    pinned = "harbor.corp/lib/redis@sha256:src"
    assert f"image mod {pinned} --create harbor.corp/lib/redis:7.2.0" in line
    assert "--replace" not in line
    # the digest is read back from the published tag, not from the pinned input
    assert "image digest harbor.corp/lib/redis:7.2.0" in line
    assert result == "sha256:abc123"


def test_delete_tag_invokes_tag_rm(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().delete_tag("harbor.corp/lib/redis:6.0.0")
    assert "tag rm harbor.corp/lib/redis:6.0.0" in log.read_text()


def test_write_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().copy("a:1", "b:1")


# Fix 1 — created edge cases (→ None contract for Phase 7's pushed_at)


def test_inspect_invalid_created_is_none(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "created-invalid")
    assert RegctlAdapter().inspect("x:1").created is None


def test_inspect_absent_created_is_none(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "no-created")
    assert RegctlAdapter().inspect("x:1").created is None


# Fix 2 — shutil.which → None branch


def test_no_binary_in_path_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Résolution lazy : la construction réussit dans un env sans regctl ; l'erreur
    # ne survient qu'au premier appel (pour ne pas bloquer build_container).
    monkeypatch.setenv("PATH", "")
    adapter = RegctlAdapter()
    with pytest.raises(RegctlError, match="not found in PATH"):
        adapter.list_tags("docker.io/redis")


# Fix 3 — _json non-dict branch


def test_inspect_non_object_json_raises(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "manifest-array")
    with pytest.raises(RegctlError, match="expected JSON object"):
        RegctlAdapter().inspect("x:1")


# Fix 4 — annotation value containing '='


def test_annotate_value_with_equals_is_passed_through(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().annotate("r:1", {"k": "a=b=c"})
    assert "--annotation k=a=b=c" in log.read_text()


def test_login_invokes_registry_login_with_password_on_stdin(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().login("harbor.corp", username="robot", password="s3cret", tls_verify=True)
    line = log.read_text()
    assert "registry login --user robot --pass-stdin harbor.corp" in line
    assert "s3cret" not in line  # password is on stdin, never in argv


def test_login_tls_disabled(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().login("localhost:5000", username="u", password="p", tls_verify=False)
    assert "--tls disabled" in log.read_text()


def test_configure_registry_tls_disabled_with_cacert(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().configure_registry("localhost:5000", tls_verify=False, ca_cert="/etc/ca.pem")
    assert "registry set localhost:5000 --tls disabled --cacert /etc/ca.pem" in log.read_text()


def test_configure_registry_tls_enabled_no_cacert(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().configure_registry("harbor.corp", tls_verify=True, ca_cert=None)
    text = log.read_text()
    assert "registry set harbor.corp --tls enabled" in text
    assert "--cacert" not in text


def test_put_referrer_invokes_artifact_put_with_subject(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().put_referrer(
        "harbor.corp/lib/redis:6.0.0",
        "application/vnd.knock.lifecycle.pending+json",
        {"io.knock.lifecycle.state": "pending-deletion"},
    )
    line = log.read_text()
    assert "artifact put" in line
    assert "--subject harbor.corp/lib/redis:6.0.0" in line
    assert "--artifact-type application/vnd.knock.lifecycle.pending+json" in line
    assert "--annotation io.knock.lifecycle.state=pending-deletion" in line


def test_delete_referrer_invokes_manifest_delete(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    RegctlAdapter().delete_referrer("harbor.corp/lib/redis@sha256:ref1")
    assert "manifest delete harbor.corp/lib/redis@sha256:ref1" in log.read_text()


def test_list_referrers_parses_descriptors(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "referrers-one")
    got = RegctlAdapter().list_referrers(
        "harbor.corp/lib/redis:6.0.0", "application/vnd.knock.lifecycle.pending+json"
    )
    assert len(got) == 1
    assert got[0].digest == "sha256:ref1"
    assert got[0].artifact_type == "application/vnd.knock.lifecycle.pending+json"
    assert got[0].subject_tag == "harbor.corp/lib/redis:6.0.0"


def test_list_referrers_empty_when_none(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "referrers-empty")
    assert (
        RegctlAdapter().list_referrers("harbor.corp/lib/redis:6.0.0", "application/vnd.knock.x")
        == []
    )


def test_list_referrers_unfiltered_omits_filter_flag(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "referrers-one")
    RegctlAdapter().list_referrers("harbor.corp/lib/redis:6.0.0")  # no artifact_type
    line = next(ln for ln in log.read_text().splitlines() if ln.startswith("artifact list"))
    assert "--filter-artifact-type" not in line


def test_put_referrer_with_blob_invokes_artifact_put_with_flags(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = _log(tmp_path, monkeypatch)
    digest = RegctlAdapter().put_referrer(
        "harbor.corp/lib/redis@sha256:abc",
        "application/vnd.knock.scan.result.v1",
        {"io.knock.scan.tool": "trivy", "io.knock.scan.vuln.critical": "0"},
        blob=b'{"runs": []}',
        media_type="application/sarif+json",
    )
    line = log.read_text()
    assert "artifact put" in line
    assert "--subject harbor.corp/lib/redis@sha256:abc" in line
    assert "--artifact-type application/vnd.knock.scan.result.v1" in line
    assert "--file-media-type application/sarif+json" in line
    assert "--annotation io.knock.scan.tool=trivy" in line
    assert "--annotation io.knock.scan.vuln.critical=0" in line
    assert line.split().count("harbor.corp/lib/redis@sha256:abc") == 1
    assert digest == "harbor.corp/lib/redis@sha256:ref123"


def test_put_referrer_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().put_referrer("r:1", "application/vnd.knock.x", {})


def test_delete_referrer_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().delete_referrer("harbor.corp/lib/redis@sha256:ref1")


def test_list_referrers_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().list_referrers("harbor.corp/lib/redis:6.0.0", "application/vnd.knock.x")


def test_list_repositories_parses_lines(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "repos")
    adapter = RegctlAdapter(str(fake_bin_path / "regctl"))
    assert adapter.list_repositories("harbor.example") == ["lib/redis", "lib/nginx"]


def test_list_repositories_empty_registry(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "empty")
    adapter = RegctlAdapter(str(fake_bin_path / "regctl"))
    assert adapter.list_repositories("harbor.example") == []


def test_put_referrer_with_blob_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().put_referrer(
            "r@sha256:abc",
            "t",
            {},
            blob=b"{}",
            media_type="m",
        )


def test_get_annotations_returns_digest_and_annotations(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "mirror-stamped")
    digest, ann = RegctlAdapter().get_annotations("harbor.example/lib/redis:7.2")
    assert ann == {"org.opencontainers.image.base.digest": "sha256:src999"}
    assert digest == "sha256:abc123"


def test_get_annotations_empty_when_none(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    assert RegctlAdapter().get_annotations("harbor.example/lib/redis:7.2") == ("sha256:abc123", {})


def test_get_annotations_failure_raises_regctl_error(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "fail")
    with pytest.raises(RegctlError):
        RegctlAdapter().get_annotations("harbor.example/lib/redis:7.2")


def test_inspect_reads_config_labels(fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "config-labels")
    info = RegctlAdapter().inspect("docker.io/library/redis:7.2.0")
    assert info.config_labels["org.opencontainers.image.revision"] == "9fceb02commit"


def test_inspect_no_config_labels_is_empty(
    fake_bin_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_REGCTL_SCENARIO", "default")
    assert RegctlAdapter().inspect("x:1").config_labels == {}
