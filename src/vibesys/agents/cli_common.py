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

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

from vibesys.agent_runner import log_and_print
from vibesys.constants import ComputeBackend
from vibesys.skills import foreign_platform_names, is_platforms_parent

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

_NATIVE_SCHEMA_DIR = Path(".cache/vibesys/response-schemas")

# Codex's native response format accepts the object/array/scalar subset used
# by Pydantic's ordinary model schemas. Reject constructs that require schema
# evaluation features outside that subset instead of discovering the problem
# after an expensive agent turn has started. Field names are not inspected as
# keywords; ``properties`` and ``$defs`` are traversed as maps of subschemas.
_UNSUPPORTED_NATIVE_SCHEMA_KEYWORDS = frozenset(
    {
        "$anchor",
        "$dynamicAnchor",
        "$dynamicRef",
        "$id",
        "$schema",
        "allOf",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "maxContains",
        "minContains",
        "not",
        "oneOf",
        "patternProperties",
        "prefixItems",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def _validate_native_output_schema(schema: object) -> dict[str, object]:
    """Normalize and validate the strict JSON Schema subset used by Codex.

    Native structured output requires every declared object property and
    forbids undeclared keys. Pydantic omits defaulted properties from
    ``required`` and represents arbitrary mappings with a schema-valued
    ``additionalProperties``; the former is normalized here while the latter
    falls back to the portable prompt contract.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("native output schema must have an object root")

    def visit(node: object, location: str) -> None:
        if isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{location}/{index}")
            return
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise ValueError(f"native output schema uses a non-local $ref at {location}")
            # Codex follows the older strict subset where a reference may not
            # have annotation or validation siblings.
            node.clear()
            node["$ref"] = reference
            return
        node.pop("default", None)
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise ValueError(f"native output schema {location}/properties must be an object")
            additional = node.get("additionalProperties")
            if additional not in (None, False):
                raise ValueError(f"native output schema uses arbitrary object keys at {location}")
            node["additionalProperties"] = False
            node["required"] = list(properties)
        elif node.get("type") == "object":
            additional = node.get("additionalProperties")
            if additional not in (None, False):
                raise ValueError(f"native output schema uses arbitrary object keys at {location}")
            node["additionalProperties"] = False
            node["required"] = []
        for key, value in node.items():
            if key in {"properties", "$defs", "definitions"}:
                if not isinstance(value, dict):
                    raise ValueError(f"native output schema {location}/{key} must be an object")
                for name, subschema in value.items():
                    visit(subschema, f"{location}/{key}/{name}")
                continue
            if key in _UNSUPPORTED_NATIVE_SCHEMA_KEYWORDS:
                raise ValueError(
                    f"native output schema uses unsupported keyword {key!r} at {location}"
                )
            visit(value, f"{location}/{key}")

    visit(schema, "#")
    return schema


def materialize_native_output_schema(workspace: Path, response_cls: type[BaseModel]) -> str:
    """Atomically write a validated schema and return its relative CLI path."""
    schema = _validate_native_output_schema(response_cls.model_json_schema())
    encoded = (json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    relative = _NATIVE_SCHEMA_DIR / f"{response_cls.__name__}-{digest}.json"
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return relative.as_posix()


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


def _platform_prune_ignore(
    compute_backend: ComputeBackend | None,
) -> Callable[[str, list[str]], set[str]]:
    """Build a ``copytree`` ignore callable that prunes foreign platforms."""
    skip_names = {".git", "repos", "__pycache__"}
    foreign = foreign_platform_names(compute_backend)

    def _ignore(src_dir: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in skip_names}
        if foreign and is_platforms_parent(src_dir):
            ignored |= {name for name in names if name in foreign}
        return ignored

    return _ignore


def materialize_skills(
    workspace: Path,
    skill_dirs: list[Path],
    *,
    compute_backend: ComputeBackend | None = None,
    log_file: TextIO | None = None,
) -> None:
    """Copy each skill directory into the workspace and CLI discovery paths.

    Walks each ``skill_dirs`` entry for ``SKILL.md`` files and flattens each
    parent directory into the workspace root and every path under
    :data:`CLI_SKILL_DIRS` (one per CLI convention: ``.claude/skills``,
    ``.agents/skills``, ``.gemini/skills``, ``.cursor/skills``,
    ``.opencode/skills``). The root copy preserves the documented
    ``<skill-name>/references/...`` paths used by prompts and agents, while the
    hidden copies support native CLI discovery. When a compute backend is set,
    foreign ``references/platforms/<backend>/`` directories are omitted from
    every materialized copy.

    Existing destinations are replaced on every invocation so skill edits are
    picked up across iterations and after candidate checkpoint rollback. Errors
    are logged but never raised — the loop should still make progress even if a
    skill fails to materialize.
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

    skip_ignore = _platform_prune_ignore(compute_backend)

    for target_rel in (".", *CLI_SKILL_DIRS):
        target_root = workspace / target_rel
        target_root.mkdir(parents=True, exist_ok=True)
        for name, src_skill in discovered.items():
            dest = target_root / name
            try:
                if src_skill.resolve() == dest.resolve():
                    continue
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
