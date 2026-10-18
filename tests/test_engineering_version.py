from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

import yaml


FULL_SHA = "47f3fd9e56b560eba2804b99db348d883f1abdf3"
OTHER_SHA = "a" * 40


def test_dockerfile_bakes_runtime_version_and_oci_revision():
    dockerfile = Path("Dockerfile").read_text()
    runtime = dockerfile.split("FROM python:3.12.11-slim-bookworm", 2)[2]

    assert "ARG APP_VERSION=unknown" in runtime
    assert "APP_VERSION=$APP_VERSION" in runtime
    assert "org.opencontainers.image.revision=$APP_VERSION" in runtime


def test_compose_builds_development_version_once_for_shared_image():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    app = compose["services"]["app"]
    worker = compose["services"]["radar-worker"]

    assert app["build"]["args"]["APP_VERSION"] == "${APP_VERSION:-development}"
    assert worker["image"] == app["image"]
    assert "build" not in worker
    assert "ports" not in worker


def test_update_exports_pulled_head_before_build_and_up():
    script = Path("scripts/update.sh").read_text()
    pull = script.index('git pull --ff-only origin "$branch"')
    version = script.index('APP_VERSION=$(git rev-parse HEAD)')
    export = script.index("export APP_VERSION")
    build = script.index("docker compose build")
    up = script.index("docker compose up")

    assert pull < version < export < build < up
    assert FULL_SHA not in script


def test_rollback_exports_detached_head_before_build_and_up():
    script = Path("scripts/rollback.sh").read_text()
    switch = script.index('git switch --detach "$ref"')
    version = script.index('APP_VERSION=$(git rev-parse HEAD)')
    export = script.index("export APP_VERSION")
    build = script.index("docker compose build")
    up = script.index("docker compose up")

    assert switch < version < export < build < up
    assert FULL_SHA not in script


def test_healthcheck_rejects_container_version_mismatch_without_docker():
    with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
        fake_bin = Path(directory)
        command = fake_bin / "fake-command"
        command.write_text(
            "#!/bin/sh\n"
            "case \"$(basename \"$0\"):$*\" in\n"
            f"  git:*) printf '%s\\n' '{FULL_SHA}' ;;\n"
            "  docker:*exec*-T*app*printenv*APP_VERSION*) "
            f"printf '%s\\n' '{FULL_SHA}' ;;\n"
            "  docker:*exec*-T*radar-worker*printenv*APP_VERSION*) "
            f"printf '%s\\n' '{OTHER_SHA}' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        command.chmod(0o755)
        for name in ("git", "docker", "curl"):
            os.link(command, fake_bin / name)

        result = subprocess.run(
            ["sh", "scripts/healthcheck.sh"],
            cwd=Path.cwd(),
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
        )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert f"Host git HEAD: {FULL_SHA}" in output
    assert f"App APP_VERSION: {FULL_SHA}" in output
    assert f"Radar worker APP_VERSION: {OTHER_SHA}" in output
    assert "Engineering version mismatch" in output
