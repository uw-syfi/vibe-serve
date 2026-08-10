"""Tests for wheel-time resource staging and run-time resource resolution."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003  # tracked: #288

# `resources_packaging` is a repo-root, build-time module (imported by
# setup.py). It is not part of the installed package, so pyright's project
# roots cannot resolve it; pytest picks it up via `pythonpath = ["."]`.
from resources_packaging import (  # pyright: ignore[reportMissingImports]
    stage_resources,
)

from vibesys import resource_paths
from vibesys.constants import PROJECT_ROOT


def _make_fake_repo(root: Path) -> Path:
    (root / "resources" / "profilers" / "nsys").mkdir(parents=True)
    (root / "resources" / "profilers" / "nsys" / "server.py").write_text("# server")
    (root / "resources" / "profilers" / "nsys" / "__pycache__").mkdir()
    (root / "resources" / "profilers" / "nsys" / "__pycache__" / "server.pyc").write_text("x")
    skill = root / "resources" / "skills" / "serving-systems"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: serving-systems\ndescription: d\n---\n")
    (skill / ".vibesys.toml").write_text('[[rule]]\npath = "."\n')
    (skill / "repos" / "vllm").mkdir(parents=True)
    (skill / "repos" / "vllm" / "big.bin").write_text("vendored checkout")
    return root


def test_stage_resources_copies_trees_and_drops_vendored_checkouts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo = _make_fake_repo(tmp_path / "repo")
    dest = tmp_path / "build" / "vibesys" / "_resources"

    assert stage_resources(repo, dest)
    assert (dest / "profilers" / "nsys" / "server.py").is_file()
    assert (dest / "skills" / "serving-systems" / "SKILL.md").is_file()
    assert (dest / "skills" / "serving-systems" / ".vibesys.toml").is_file()
    assert not (dest / "skills" / "serving-systems" / "repos").exists()
    assert not (dest / "profilers" / "nsys" / "__pycache__").exists()


def test_stage_resources_is_idempotent(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    repo = _make_fake_repo(tmp_path / "repo")
    dest = tmp_path / "dest"

    assert stage_resources(repo, dest)
    assert stage_resources(repo, dest)
    assert (dest / "profilers" / "nsys" / "server.py").is_file()


def test_stage_resources_without_resources_dir_is_a_noop(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    dest = tmp_path / "dest"

    assert not stage_resources(tmp_path / "empty-repo", dest)
    assert not dest.exists()


def test_resources_root_prefers_the_checkout():  # noqa: ANN201  # tracked: #288
    assert resource_paths.resources_root() == PROJECT_ROOT / "resources"


def test_profiler_support_dir_resolves_known_kind_and_rejects_unknown():  # noqa: ANN201  # tracked: #288
    nsys = resource_paths.profiler_support_dir("nsys")
    assert nsys is not None
    assert (nsys / "server.py").is_file()
    assert resource_paths.profiler_support_dir("no-such-profiler") is None


def test_default_skill_roots_point_at_the_resources_tree():  # noqa: ANN201  # tracked: #288
    roots = resource_paths.default_skill_roots()
    assert roots == (PROJECT_ROOT / "resources" / "skills",)


def test_resources_root_falls_back_to_the_staged_wheel_copy(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    fake_checkout = tmp_path / "no-checkout"
    fake_package = tmp_path / "site-packages" / "vibesys"
    staged = fake_package / "_resources"
    (staged / "profilers" / "nsys").mkdir(parents=True)
    (staged / "profilers" / "nsys" / "server.py").write_text("# server")
    (staged / "skills").mkdir()

    monkeypatch.setattr(resource_paths, "PROJECT_ROOT", fake_checkout)
    monkeypatch.setattr(resource_paths, "files", lambda _package: fake_package)

    assert resource_paths.resources_root() == staged
    support = resource_paths.profiler_support_dir("nsys")
    assert support == staged / "profilers" / "nsys"
    assert resource_paths.default_skill_roots() == (staged / "skills",)


def test_resources_root_is_none_without_checkout_or_staged_copy(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(resource_paths, "PROJECT_ROOT", tmp_path / "nowhere")
    monkeypatch.setattr(resource_paths, "files", lambda _package: tmp_path / "no-package")

    assert resource_paths.resources_root() is None
    assert resource_paths.profiler_support_dir("nsys") is None
    assert resource_paths.default_skill_roots() == ()
