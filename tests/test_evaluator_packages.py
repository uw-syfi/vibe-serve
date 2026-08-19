"""Tests for versioned evaluator package contracts and local resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from vibesys.evaluators import (
    EvaluatorPackageError,
    EvaluatorPackageNotFoundError,
    EvaluatorPackageRegistry,
    EvaluatorPackageRequirement,
    load_evaluator_package,
    resolve_evaluator_package,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_package(
    root: Path,
    *,
    name: str = "vibesys-evaluator-test",
    version: str = "1.2.3",
    extra_metadata: str = "",
) -> Path:
    root.mkdir(parents=True)
    (root / "runner.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "vibesys.evaluator.toml").write_text(
        f'''schema_version = 1
name = "{name}"
version = "{version}"
protocol_version = 1
{extra_metadata}
[entrypoints]
test-check = ["python", "${{PACKAGE_ROOT}}/runner.py"]
''',
        encoding="utf-8",
    )
    return root


def test_load_package_validates_metadata_and_expands_entrypoint(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "packages" / "test")

    package = load_evaluator_package(root)

    assert package.name == "vibesys-evaluator-test"
    assert package.version == "1.2.3"
    assert package.digest.startswith("sha256:")
    assert package.command("test-check", "--case", "smoke") == (
        "python",
        f"{root}/runner.py",
        "--case",
        "smoke",
    )
    assert package.command(
        "test-check",
        "--project",
        "${PROJECT_ROOT}",
        project_root=tmp_path,
    )[-2:] == ("--project", str(tmp_path))


def test_digest_covers_paths_contents_and_executable_mode(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "package")
    original = load_evaluator_package(root).digest

    runner = root / "runner.py"
    runner.write_text("print('changed')\n", encoding="utf-8")
    changed_contents = load_evaluator_package(root).digest
    runner.chmod(0o755)
    changed_mode = load_evaluator_package(root).digest

    assert len({original, changed_contents, changed_mode}) == 3


def test_digest_ignores_interpreter_cache(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "package")
    original = load_evaluator_package(root).digest

    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "runner.cpython-312.pyc").write_bytes(b"generated")

    assert load_evaluator_package(root).digest == original


def test_package_rejects_symlinks(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "package")
    (root / "runner-link.py").symlink_to(root / "runner.py")

    with pytest.raises(EvaluatorPackageError, match="may not contain symlinks"):
        load_evaluator_package(root)


def test_registry_resolves_an_exact_version(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    expected = _write_package(packages / "v1", version="1.0.0")
    _write_package(packages / "v2", version="2.0.0")

    package = EvaluatorPackageRegistry(packages).resolve(
        EvaluatorPackageRequirement(name="vibesys-evaluator-test", version="1.0.0")
    )

    assert package.root == expected
    assert package.version == "1.0.0"


def test_registry_reports_available_versions(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    _write_package(packages / "v1", version="1.0.0")

    with pytest.raises(
        EvaluatorPackageNotFoundError,
        match=r"vibesys-evaluator-test==2\.0\.0.*available packages: "
        r"vibesys-evaluator-test==1\.0\.0",
    ):
        EvaluatorPackageRegistry(packages).resolve(
            EvaluatorPackageRequirement(name="vibesys-evaluator-test", version="2.0.0")
        )


def test_registry_rejects_duplicate_name_and_version(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    _write_package(packages / "first")
    _write_package(packages / "second")

    with pytest.raises(EvaluatorPackageError, match="duplicate evaluator package"):
        EvaluatorPackageRegistry(packages).resolve(
            EvaluatorPackageRequirement(name="vibesys-evaluator-test", version="1.2.3")
        )


def test_metadata_rejects_unknown_fields(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "package", extra_metadata="unknown = true\n")

    with pytest.raises(EvaluatorPackageError, match="invalid evaluator package metadata"):
        load_evaluator_package(root)


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("Uppercase", "1.0.0"),
        ("vibesys-evaluator-test", "^1.0"),
        ("vibesys evaluator test", "1.0.0"),
    ],
)
def test_requirement_rejects_noncanonical_values(name: str, version: str) -> None:
    with pytest.raises(ValidationError):
        EvaluatorPackageRequirement(name=name, version=version)


def test_unknown_entrypoint_lists_available_names(tmp_path: Path) -> None:
    package = load_evaluator_package(_write_package(tmp_path / "package"))

    with pytest.raises(EvaluatorPackageError, match="available entrypoints: test-check"):
        package.command("missing")


def test_framework_resolver_finds_bundled_queue_package() -> None:
    package = resolve_evaluator_package(
        EvaluatorPackageRequirement(name="vibesys-evaluator-queue", version="0.1.0")
    )

    assert package.root.name == "queue"
    assert package.command("vibesys-queue")[:3] == ("go", "-C", str(package.root))


@pytest.mark.parametrize(
    ("name", "entrypoints"),
    [
        ("vibesys-evaluator-queue", {"vibesys-queue"}),
        (
            "vibesys-evaluator-microservice",
            {"servicebench", "otelinject", "otelcapture"},
        ),
    ],
)
def test_bundled_evaluator_package_metadata(name: str, entrypoints: set[str]) -> None:
    package = resolve_evaluator_package(EvaluatorPackageRequirement(name=name, version="0.1.0"))

    assert set(package.metadata.entrypoints) == entrypoints
    assert len(package.digest) == len("sha256:") + 64


def test_package_digest_ignores_rust_build_output(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "package")
    before = load_evaluator_package(root).digest
    artifact = root / "native_runner" / "target" / "debug" / "runner"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"machine-local build output")

    assert load_evaluator_package(root).digest == before


def test_bundled_evaluator_packages_declare_only_required_toolchains() -> None:
    queue = resolve_evaluator_package(
        EvaluatorPackageRequirement(name="vibesys-evaluator-queue", version="0.1.0")
    )
    microservice = resolve_evaluator_package(
        EvaluatorPackageRequirement(name="vibesys-evaluator-microservice", version="0.1.0")
    )

    assert queue.metadata.toolchains == ("go", "rust")
    assert microservice.metadata.toolchains == ("go",)
