"""Tests for the helpers shared by the agentshim and Omnigent CLI runners.

These moved out of ``cli_runner.py`` unchanged when the Omnigent backend needed
the same behavior. The cases here cover the branches that both backends depend
on — skill discovery across layouts, replacement on re-materialization, and the
deliberate never-raise policy on copy failures.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from vibesys.agents.cli_common import (
    CLI_SKILL_DIRS,
    agent_label,
    build_schema_hint,
    discover_skill_dirs,
    materialize_skills,
)
from vibesys.schemas import JudgeResponse


def _skill(root: Path, name: str, body: str = "# skill\n") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    return d


class TestAgentLabel:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("perf_eval", "Perf Eval"), ("judge", "Judge"), ("implementer", "Implementer")],
    )
    def test_snake_case_becomes_title_case(self, kind, expected):
        assert agent_label(kind) == expected


class TestDiscoverSkillDirs:
    def test_a_root_that_is_itself_a_skill(self, tmp_path):
        (tmp_path / "SKILL.md").write_text("# s\n")

        assert discover_skill_dirs(tmp_path) == [tmp_path]

    def test_flat_layout(self, tmp_path):
        a = _skill(tmp_path, "alpha")
        b = _skill(tmp_path, "beta")

        assert sorted(discover_skill_dirs(tmp_path)) == sorted([a, b])

    def test_tier_organized_layout(self, tmp_path):
        deep = _skill(tmp_path, "tier1/nested")

        assert discover_skill_dirs(tmp_path) == [deep]

    def test_a_root_with_no_skills(self, tmp_path):
        (tmp_path / "notes.txt").write_text("x")

        assert discover_skill_dirs(tmp_path) == []


class TestMaterializeSkills:
    def test_copies_into_every_cli_discovery_path(self, tmp_path):
        src = tmp_path / "src"
        _skill(src, "alpha")
        ws = tmp_path / "ws"
        ws.mkdir()

        materialize_skills(ws, [src])

        for rel in CLI_SKILL_DIRS:
            assert (ws / rel / "alpha" / "SKILL.md").is_file()

    def test_no_sources_is_a_noop(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()

        materialize_skills(ws, [])

        assert list(ws.iterdir()) == []

    def test_sources_without_skills_create_nothing(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "readme.txt").write_text("x")
        ws = tmp_path / "ws"
        ws.mkdir()

        materialize_skills(ws, [src])

        assert list(ws.iterdir()) == []

    def test_re_materializing_replaces_stale_content(self, tmp_path):
        src = tmp_path / "src"
        _skill(src, "alpha", "# v1\n")
        ws = tmp_path / "ws"
        ws.mkdir()
        materialize_skills(ws, [src])

        (src / "alpha" / "SKILL.md").write_text("# v2\n")
        materialize_skills(ws, [src])

        assert (ws / ".claude/skills/alpha/SKILL.md").read_text() == "# v2\n"

    def test_later_source_wins_on_a_name_collision(self, tmp_path):
        first = tmp_path / "a"
        second = tmp_path / "b"
        _skill(first, "dup", "# first\n")
        _skill(second, "dup", "# second\n")
        ws = tmp_path / "ws"
        ws.mkdir()

        materialize_skills(ws, [first, second])

        assert (ws / ".claude/skills/dup/SKILL.md").read_text() == "# second\n"

    def test_a_copy_failure_is_logged_not_raised(self, tmp_path, monkeypatch):
        """The loop must still make progress if one skill fails to copy."""
        src = tmp_path / "src"
        _skill(src, "alpha")
        ws = tmp_path / "ws"
        ws.mkdir()
        log = StringIO()

        def _boom(*_args, **_kwargs):
            raise OSError("disk on fire")

        monkeypatch.setattr("vibesys.agents.cli_common.shutil.copytree", _boom)

        materialize_skills(ws, [src], log_file=log)

        written = log.getvalue()
        assert "failed to materialize" in written
        assert "disk on fire" in written

    def test_a_copy_failure_without_a_log_is_swallowed(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        _skill(src, "alpha")
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr(
            "vibesys.agents.cli_common.shutil.copytree",
            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")),
        )

        materialize_skills(ws, [src])  # must not raise

    def test_a_stale_symlink_destination_is_replaced(self, tmp_path):
        src = tmp_path / "src"
        _skill(src, "alpha")
        ws = tmp_path / "ws"
        dest = ws / ".claude/skills"
        dest.mkdir(parents=True)
        (dest / "alpha").symlink_to(tmp_path / "does-not-exist")

        materialize_skills(ws, [src])

        assert (dest / "alpha" / "SKILL.md").is_file()
        assert not (dest / "alpha").is_symlink()


class TestBuildSchemaHint:
    def test_names_the_model_and_embeds_its_schema(self):
        hint = build_schema_hint(JudgeResponse)

        assert "JudgeResponse" in hint
        assert "EXACTLY one JSON object" in hint
        assert "verdict" in hint

    def test_forbids_markdown_fences(self):
        """CLI tools wrap JSON in fences unless told not to."""
        assert "Do not wrap it in markdown fences" in build_schema_hint(JudgeResponse)
