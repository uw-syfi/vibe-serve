"""Contracts for assembling one native self-contained release wheel."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.build_release_wheel import (  # pyright: ignore[reportMissingImports]
    BuildEnvironment,
    ReleaseBuildError,
    build_release_wheel,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_build_script_can_be_invoked_by_file_path(tmp_path: Path) -> None:
    """The documented ``python scripts/...`` entry point must import its helpers."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(PROJECT_ROOT / "scripts/build_release_wheel.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "--target TARGET" in result.stdout


def _make_repo(root: Path) -> tuple[Path, Path]:
    tui = root / "clients" / "tui"
    (tui / "dist").mkdir(parents=True)
    (tui / "package.json").write_text(
        json.dumps({"name": "@vibesys/tui", "version": "0.1.0"})
    )
    (tui / "dist" / "launcher.js").write_text("// launcher\n")
    (tui / "dist" / "self-test.js").write_text("// self-test\n")
    (root / "pyproject.toml").write_text('[project]\nname = "vibesys"\nversion = "0.1.0"\n')
    license_path = root / "third_party" / "bun" / "LICENSE.md"
    license_path.parent.mkdir(parents=True)
    license_path.write_text("Bun license\n")
    bun = root / "tools" / "bun"
    bun.parent.mkdir()
    bun.write_text("binary")
    bun.chmod(0o755)
    return root, bun


def _fake_deployment(destination: Path) -> None:
    (destination / "dist").mkdir(parents=True)
    (destination / "dist" / "launcher.js").write_text("// launcher\n")
    (destination / "dist" / "launcher.js.map").write_text("{}\n")
    (destination / "dist" / "self-test.js").write_text("// self-test\n")
    (destination / "package.json").write_text(
        json.dumps({"name": "@vibesys/tui", "version": "0.1.0"})
    )
    opentui = destination / "node_modules" / "@opentui"
    (opentui / "core").mkdir(parents=True)
    (opentui / "core" / "index.js").write_text("// core\n")
    (opentui / "core" / "LICENSE").write_text("OpenTUI license\n")
    for package in ("core-linux-x64", "core-linux-arm64", "core-darwin-arm64"):
        native = opentui / package
        native.mkdir()
        (native / "index.js").write_text("// native\n")


def test_build_release_wheel_assembles_payload_without_mutating_node_modules(tmp_path):  # noqa: ANN001, ANN201
    repo, bun = _make_repo(tmp_path / "repo")
    sentinel = repo / "node_modules" / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_text("untouched\n")
    output = tmp_path / "dist"
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []
    payload_snapshot: dict[str, object] = {}

    def runner(command, *, cwd, check, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        argv = [str(part) for part in command]
        env = kwargs.get("env")
        calls.append((argv, Path(cwd), env))
        if argv == [str(bun), "--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="1.3.9\n", stderr="")
        if "deploy" in argv:
            _fake_deployment(Path(argv[-1]))
        if argv[:3] == ["uv", "build", "--wheel"]:
            assert env is not None
            payload = Path(env["VIBESYS_TUI_BUNDLE"])
            payload_snapshot["manifest"] = json.loads((payload / "manifest.json").read_text())
            payload_snapshot["files"] = {
                path.relative_to(payload).as_posix() for path in payload.rglob("*")
            }
            wheel = output / "vibesys-0.1.0-py3-none-manylinux_2_28_x86_64.whl"
            wheel.parent.mkdir(parents=True, exist_ok=True)
            wheel.write_bytes(b"wheel")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    wheel = build_release_wheel(
        repo,
        target_key="linux-x86_64",
        bun=bun,
        output_dir=output,
        environment=BuildEnvironment(
            runner=runner,
            host_system="Linux",
            host_machine="x86_64",
        ),
    )

    assert wheel.name == "vibesys-0.1.0-py3-none-manylinux_2_28_x86_64.whl"
    assert sentinel.read_text() == "untouched\n"
    commands = [call[0] for call in calls]
    assert ["pnpm", "install", "--frozen-lockfile"] in commands
    assert ["pnpm", "--dir", "clients/tui", "build"] in commands
    deploy = next(command for command in commands if "deploy" in command)
    assert "--prod" in deploy
    assert "--legacy" not in deploy
    build_call = next(call for call in calls if call[0][:3] == ["uv", "build", "--wheel"])
    assert build_call[2] is not None
    assert build_call[2]["VIBESYS_WHEEL_TARGET"] == "linux-x86_64"
    manifest = payload_snapshot["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["bun_version"] == "1.3.9"
    assert manifest["target"] == "linux-x86_64"
    files = payload_snapshot["files"]
    assert isinstance(files, set)
    assert "app/dist/launcher.js.map" not in files
    assert "app/node_modules/@opentui/core-linux-x64" in files
    assert "app/node_modules/@opentui/core-linux-arm64" not in files


def test_build_release_wheel_rejects_the_wrong_bun_version(tmp_path):  # noqa: ANN001, ANN201
    repo, bun = _make_repo(tmp_path / "repo")

    def runner(command, *, cwd, check, **_kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        return subprocess.CompletedProcess(command, 0, stdout="1.3.8\n", stderr="")

    with pytest.raises(ReleaseBuildError, match=r"Bun 1\.3\.9"):
        build_release_wheel(
            repo,
            target_key="linux-x86_64",
            bun=bun,
            output_dir=tmp_path / "dist",
            environment=BuildEnvironment(
                runner=runner,
                host_system="Linux",
                host_machine="x86_64",
            ),
        )


def test_build_release_wheel_rejects_preexisting_wheels(tmp_path):  # noqa: ANN001, ANN201
    repo, bun = _make_repo(tmp_path / "repo")
    output = tmp_path / "dist"
    output.mkdir()
    (output / "old.whl").write_bytes(b"old")

    with pytest.raises(ReleaseBuildError, match="already contains"):
        build_release_wheel(
            repo,
            target_key="linux-x86_64",
            bun=bun,
            output_dir=output,
            environment=BuildEnvironment(
                host_system="Linux",
                host_machine="x86_64",
            ),
        )


def test_build_release_wheel_rejects_a_universal_wheel_tag(tmp_path):  # noqa: ANN001, ANN201
    repo, bun = _make_repo(tmp_path / "repo")
    output = tmp_path / "dist"

    def runner(command, *, cwd, check, **_kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        argv = [str(part) for part in command]
        if argv == [str(bun), "--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="1.3.9\n", stderr="")
        if "deploy" in argv:
            _fake_deployment(Path(argv[-1]))
        if argv[:3] == ["uv", "build", "--wheel"]:
            output.mkdir(parents=True, exist_ok=True)
            (output / "vibesys-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(ReleaseBuildError, match="platform tag"):
        build_release_wheel(
            repo,
            target_key="linux-x86_64",
            bun=bun,
            output_dir=output,
            environment=BuildEnvironment(
                runner=runner,
                host_system="Linux",
                host_machine="x86_64",
            ),
        )
