"""Prompt and workspace helpers shared by the CLI-shaped agent backends.

Both :mod:`vibesys.agents.cli_runner` (agentshim, the default) and
:mod:`vibesys.agents.omnigent.runner` (opt-in) drive an external coding-agent
CLI against a VibeSys workspace. The pieces that decide *what the agent sees* —
which skill directories are materialized where, and how a structured-response
request is phrased — belong to that shared shape rather than to either backend,
so they live here and have exactly one definition.

Backend-specific concerns (executors, sessions, event handling, Docker
routing) stay in the individual runner modules.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

from vibesys.agent_runner import log_and_print

# Per-provider CLI skill-discovery paths, matching upstream
# vibesys-skills install.sh conventions. Each CLI tool auto-loads
# skills from a flat directory of `<skill-name>/SKILL.md`.
CLI_SKILL_DIRS: tuple[str, ...] = (
    ".claude/skills",
    ".agents/skills",
    ".gemini/skills",
    ".cursor/skills",
    ".opencode/skills",
)


def agent_label(kind: str) -> str:
    """Convert ``"perf_eval"`` to ``"Perf Eval"``, etc."""
    return kind.replace("_", " ").title()


def discover_skill_dirs(root: Path) -> list[Path]:
    """Return all skill directories reachable under *root*.

    A "skill directory" is any directory containing a ``SKILL.md`` file.
    This accepts both flat layouts (``.agents/skills/<name>/SKILL.md``) and
    the tier-organized layout from vibesys-skills
    (``skills/<tier>/<name>/SKILL.md``).
    """
    if (root / "SKILL.md").is_file():
        return [root]
    return [p.parent for p in root.rglob("SKILL.md")]


def materialize_skills(
    workspace: Path, skill_dirs: list[Path], log_file: TextIO | None = None
) -> None:
    """Copy each skill directory into the per-CLI skill-discovery paths.

    Walks each ``skill_dirs`` entry for ``SKILL.md`` files and flattens each
    parent directory into every path under :data:`CLI_SKILL_DIRS` (one per CLI
    convention: ``.claude/skills``, ``.agents/skills``, ``.gemini/skills``,
    ``.cursor/skills``, ``.opencode/skills``). This makes the skills visible
    to whichever CLI provider ends up running in the workspace without the
    caller having to know which one was picked.

    Existing destinations are replaced so skill edits are picked up across
    iterations. Errors are logged but never raised — the loop should still
    make progress even if a skill fails to materialize.
    """
    if not skill_dirs:
        return

    # Collect every skill dir across all source roots, de-duplicated by name
    # (last writer wins — matches the prior single-source behaviour when the
    # same skill name appears in multiple roots).
    discovered: dict[str, Path] = {}
    for src in skill_dirs:
        for skill_dir in discover_skill_dirs(src):
            discovered[skill_dir.name] = skill_dir

    if not discovered:
        return

    skip_names = {".git", "repos", "__pycache__"}
    skip_ignore = shutil.ignore_patterns(*skip_names)

    for target_rel in CLI_SKILL_DIRS:
        target_root = workspace / target_rel
        target_root.mkdir(parents=True, exist_ok=True)
        for name, src_skill in discovered.items():
            dest = target_root / name
            try:
                if dest.exists() or dest.is_symlink():
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.copytree(src_skill, dest, symlinks=True, ignore=skip_ignore)
            except OSError as exc:
                if log_file is not None:
                    log_and_print(
                        f"[skills] failed to materialize {src_skill} -> "
                        f"{dest}: {type(exc).__name__}: {exc}",
                        log_file,
                    )


def build_schema_hint(response_cls: type[BaseModel]) -> str:
    """Render a short instruction telling the CLI tool what JSON to emit."""
    schema = json.dumps(response_cls.model_json_schema(), indent=2)
    return (
        "\n\n--\n"
        "Return EXACTLY one JSON object that conforms to the schema below. "
        "Do not wrap it in markdown fences. Do not include any extra prose "
        "before or after the JSON object.\n\n"
        f"Schema for {response_cls.__name__}:\n{schema}\n"
    )
