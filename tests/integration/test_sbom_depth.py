"""Acceptance gate: standalone syft (knock's runtime SBOM scanner) must surface every
incident class at the right granularity. Permanent guard against silent scanner-depth
regression.

Heavyweight (docker to build the fixture + syft + network for apt/Maven). Skipped unless
syft is on PATH and the docker daemon actually answers; run locally / nightly. Mirrors the
empirical matrix in the SBOM-generation spec (ADR 0029); re-pointed from
buildkit-syft-scanner to standalone syft when SBOM generation unified on syft
(2026-06-17 spec).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest


def _docker_daemon_up() -> bool:
    """`docker` on PATH is not enough — the daemon has to answer, the way the redis_server
    fixture pings rather than trusting `which redis-server`."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _has_tools() -> bool:
    return bool(shutil.which("syft")) and bool(shutil.which("docker")) and _docker_daemon_up()


pytestmark = pytest.mark.skipif(
    not _has_tools(), reason="needs syft on PATH and a reachable docker daemon"
)

_LOG4J_URL = (
    "https://repo1.maven.org/maven2/org/apache/logging/log4j/log4j-core/2.14.1/"
    "log4j-core-2.14.1.jar"
)


def _make_nested_log4j_jar(dst: Path) -> None:
    # Spring Boot fat-jar with the real log4j-core 2.14.1 nested under BOOT-INF/lib/ — the
    # actual Log4Shell shape (a transitive dep buried inside an application archive).
    inner = dst.parent / "log4j-core-2.14.1.jar"
    try:
        urllib.request.urlretrieve(_LOG4J_URL, inner)
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"no network to fetch log4j from Maven Central: {e}")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.write(inner, "BOOT-INF/lib/log4j-core-2.14.1.jar")


def _syft_package_names(image: str) -> set[str]:
    r = subprocess.run(
        ["syft", "scan", f"docker:{image}", "-o", "spdx-json"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        pytest.fail(f"syft scan failed:\n{r.stderr}")
    doc = json.loads(r.stdout)
    return {p.get("name", "") for p in doc.get("packages", [])}


def test_sbom_depth_incident_matrix(tmp_path: Path) -> None:
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    _make_nested_log4j_jar(ctx / "app.jar")
    (ctx / "Dockerfile").write_text(
        "FROM debian:bookworm-slim\n"
        "COPY app.jar /opt/app.jar\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends openssl redis-server"
        " && rm -rf /var/lib/apt/lists/*\n"
        "RUN cp /bin/true /usr/local/bin/mongod && chmod +x /usr/local/bin/mongod\n"
    )
    image = "knock-sbom-depth-fixture:test"
    build = subprocess.run(
        ["docker", "build", "-t", image, str(ctx)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if build.returncode != 0:
        pytest.fail(f"docker build failed:\n{build.stderr}")
    try:
        names = _syft_package_names(image)
    finally:
        subprocess.run(["docker", "rmi", "-f", image], capture_output=True)

    # Present — each incident class captured at the right layer
    assert "log4j-core" in names, "Log4Shell: nested fat-JAR dependency not captured"
    assert "openssl" in names or "libssl3" in names, "Heartbleed: OS openssl not captured"
    assert "liblzma5" in names, "XZ: OS liblzma not captured"
    assert "redis-server" in names, "middleware-via-package not captured"

    # Absent — runtime (out of scope) + the documented bare-binary blind spot (kept visible)
    assert "runc" not in names, "Leaky Vessels: host runtime must not appear in an image SBOM"
    assert "mongod" not in names, "bare-binary middleware is the documented coverage blind spot"
