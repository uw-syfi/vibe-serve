"""Statically verify one self-contained VibeSys release wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from collections import Counter
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Never, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tui_packaging import BUN_VERSION, TUI_VERSION  # noqa: E402
from wheel_targets import TARGETS, WheelTarget  # noqa: E402

if TYPE_CHECKING:
    from email.message import Message

PYPI_FILE_SIZE_LIMIT = 100_000_000
FRAMEWORK_PACKAGES = (
    "vibesys",
    "vs_feature_flags",
    "vs_github",
    "vs_issue_board",
    "vs_loop_state",
    "vs_sandbox",
)
_INTERNAL_DISTRIBUTIONS = frozenset(
    {
        "vs-feature-flags",
        "vs-github",
        "vs-issue-board",
        "vs-loop-state",
        "vs-sandbox",
    }
)
_PACKAGE_SOURCE_ROOTS = {
    Path("src/vibesys"): PurePosixPath("vibesys"),
    Path("libs/vs-feature-flags/src/vs_feature_flags"): PurePosixPath("vs_feature_flags"),
    Path("libs/vs-github/src/vs_github"): PurePosixPath("vs_github"),
    Path("libs/vs-issue-board/src/vs_issue_board"): PurePosixPath("vs_issue_board"),
    Path("libs/vs-loop-state/src/vs_loop_state"): PurePosixPath("vs_loop_state"),
    Path("libs/vs-sandbox/src/vs_sandbox"): PurePosixPath("vs_sandbox"),
    Path("resources/profilers"): PurePosixPath("vibesys/_resources/profilers"),
    Path("resources/skills"): PurePosixPath("vibesys/_resources/skills"),
}
_EXCLUDED_PARTS = frozenset({".git", "__pycache__", "repos"})
_REPOSITORY_ARCHIVE_ROOTS = frozenset(
    {".git", "clients", "libs", "resources", "sdk", "src", "tests", "third_party"}
)
_REQUIRED_TUI_FILES = (
    "bin/bun",
    "app/dist/launcher.js",
    "app/dist/self-test.js",
    "app/package.json",
    "app/node_modules/@opentui/core/index.js",
    "licenses/BUN-LICENSE.md",
    "licenses/opentui-core.txt",
    "manifest.json",
)
_EXPECTED_ENTRY_POINTS = {
    "vibesys": "vibesys.cli:main",
    "vibesys-issue-mcp": "vs_issue_board.mcp:main",
}


class ReleaseWheelError(RuntimeError):
    """Raised when a wheel violates the release artifact contract."""

    @classmethod
    def unreadable_wheel(cls, wheel: Path, cause: object) -> ReleaseWheelError:
        """Build an error for an unreadable ZIP wheel."""
        return cls(f"Cannot read wheel {wheel}: {cause}")

    @classmethod
    def unreadable_project(cls, path: Path, cause: object) -> ReleaseWheelError:
        """Build an error for unreadable project metadata."""
        return cls(f"Cannot read project metadata from {path}: {cause}")

    @classmethod
    def missing_member(cls, name: str) -> ReleaseWheelError:
        """Build an error for missing wheel metadata."""
        return cls(f"Wheel is missing {name}")

    @classmethod
    def invalid_requirement(cls, cause: object) -> ReleaseWheelError:
        """Build an error for invalid dependency metadata."""
        return cls(f"Wheel contains invalid dependency metadata: {cause}")

    @classmethod
    def untracked_source_root(cls, source_root: Path) -> ReleaseWheelError:
        """Build an error for a source root Git cannot enumerate."""
        return cls(f"Cannot enumerate tracked source files in {source_root}")

    @classmethod
    def invalid_manifest(cls) -> ReleaseWheelError:
        """Build an error for malformed payload JSON."""
        return cls("TUI payload manifest.json is invalid")


def verify_wheel(wheel: Path, source_root: Path, target: WheelTarget) -> None:
    """Verify ``wheel`` against tracked sources and the requested native target."""
    wheel = wheel.resolve()
    source_root = source_root.resolve()
    project = _load_project(source_root)
    version = _project_string(project, "version")
    expected_suffix = f"-py3-none-{target.wheel_platform}.whl"
    if not wheel.name.endswith(expected_suffix):
        _fail(
            f"Wheel filename has the wrong platform tag: {wheel.name}; expected {expected_suffix}"
        )
    expected_name = f"vibesys-{version}-py3-none-{target.wheel_platform}.whl"
    if wheel.name != expected_name:
        _fail(
            f"Wheel filename version does not match pyproject.toml: "
            f"expected {expected_name}, found {wheel.name}"
        )
    if wheel.stat().st_size > PYPI_FILE_SIZE_LIMIT:
        _fail(f"Wheel exceeds the PyPI 100 MB file limit: {wheel.stat().st_size} bytes")

    dist_info = f"vibesys-{version}.dist-info"
    purelib = f"vibesys-{version}.data/purelib"
    try:
        with zipfile.ZipFile(wheel) as archive:
            infos = archive.infolist()
            _verify_archive_paths(infos)
            members = {info.filename: info for info in infos}
            metadata = _read_metadata(archive, f"{dist_info}/METADATA")
            wheel_metadata = _read_metadata(archive, f"{dist_info}/WHEEL")
            _verify_metadata(metadata, project=project, version=version)
            _verify_wheel_metadata(wheel_metadata, target=target)
            _verify_framework_packages(archive, members, purelib=purelib)
            _verify_tracked_sources(
                archive,
                members,
                source_root=source_root,
                purelib=purelib,
            )
            _verify_entry_points(archive, members, dist_info=dist_info)
            _verify_tui_payload(
                archive,
                members,
                purelib=purelib,
                target=target,
                version=version,
            )
            _required_member(members, f"{dist_info}/RECORD", "wheel RECORD")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseWheelError.unreadable_wheel(wheel, exc) from exc


def _load_project(source_root: Path) -> dict[str, object]:
    path = source_root / "pyproject.toml"
    try:
        document = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseWheelError.unreadable_project(path, exc) from exc
    project = document.get("project")
    if not isinstance(project, dict):
        _fail(f"Project metadata is missing from {path}")
    return cast("dict[str, object]", project)


def _project_string(project: dict[str, object], key: str) -> str:
    value = project.get(key)
    if not isinstance(value, str):
        _fail(f"Project metadata field {key!r} must be a string")
    return value


def _verify_archive_paths(infos: list[zipfile.ZipInfo]) -> None:
    names = [info.filename for info in infos]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        _fail(f"Wheel contains duplicate archive names: {duplicates}")
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or (path.parts and path.parts[0] in _REPOSITORY_ARCHIVE_ROOTS)
        ):
            _fail(f"Wheel contains an unsafe or repository archive path: {name!r}")
        if name.endswith(".map"):
            _fail(f"Wheel contains a source map archive path: {name}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            _fail(f"Wheel contains a symlink archive path: {name}")


def _read_metadata(archive: zipfile.ZipFile, name: str) -> Message:
    try:
        content = archive.read(name)
    except KeyError as exc:
        raise ReleaseWheelError.missing_member(name) from exc
    return BytesParser(policy=default).parsebytes(content)


def _verify_metadata(
    metadata: Message,
    *,
    project: dict[str, object],
    version: str,
) -> None:
    if metadata.get("Name") != "vibesys":
        _fail(f"Wheel METADATA has the wrong name: {metadata.get('Name')!r}")
    if metadata.get("Version") != version:
        _fail(
            f"Wheel METADATA version {metadata.get('Version')!r} "
            f"does not match project version {version!r}"
        )
    expected_python = _project_string(project, "requires-python")
    if metadata.get("Requires-Python") != expected_python:
        _fail("Wheel METADATA Requires-Python does not match pyproject.toml")

    raw_requirements = metadata.get_all("Requires-Dist", [])
    try:
        actual = Counter(str(Requirement(value)) for value in raw_requirements)
    except InvalidRequirement as exc:
        raise ReleaseWheelError.invalid_requirement(exc) from exc
    for requirement in actual:
        parsed = Requirement(requirement)
        if canonicalize_name(parsed.name) in _INTERNAL_DISTRIBUTIONS:
            _fail(f"Wheel must not depend on internal distribution {parsed.name}")

    expected_values = _project_requirements(project)
    expected = Counter(str(Requirement(value)) for value in expected_values)
    if actual != expected:
        _fail(
            "Wheel dependency metadata does not match pyproject.toml: "
            f"expected {sorted(expected.elements())}, found {sorted(actual.elements())}"
        )


def _project_requirements(project: dict[str, object]) -> list[str]:
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(value, str) for value in dependencies
    ):
        _fail("Project dependencies must be a list of strings")
    result = list(cast("list[str]", dependencies))
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        _fail("Project optional dependencies must be a table")
    for extra, values in optional.items():
        if (
            not isinstance(extra, str)
            or not isinstance(values, list)
            or not all(isinstance(value, str) for value in values)
        ):
            _fail("Project optional dependencies must contain lists of strings")
        result.extend(f'{value}; extra == "{extra}"' for value in cast("list[str]", values))
    return result


def _verify_wheel_metadata(metadata: Message, *, target: WheelTarget) -> None:
    expected_tag = f"py3-none-{target.wheel_platform}"
    if metadata.get("Root-Is-Purelib", "").lower() != "false":
        _fail("Native release wheel must declare Root-Is-Purelib: false")
    if metadata.get_all("Tag", []) != [expected_tag]:
        _fail(
            f"Wheel metadata has the wrong platform tag; expected {expected_tag!r}, "
            f"found {metadata.get_all('Tag', [])}"
        )


def _verify_framework_packages(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    *,
    purelib: str,
) -> None:
    for package in FRAMEWORK_PACKAGES:
        _required_member(
            members,
            f"{purelib}/{package}/__init__.py",
            f"framework package {package}",
        )
    for package in FRAMEWORK_PACKAGES[1:]:
        _required_member(members, f"{purelib}/{package}/py.typed", f"{package} py.typed")

    top_level_path = next(
        (name for name in members if name.endswith(".dist-info/top_level.txt")),
        None,
    )
    if top_level_path is not None:
        declared = set(archive.read(top_level_path).decode().splitlines())
        if declared != set(FRAMEWORK_PACKAGES):
            _fail(
                "Wheel top_level.txt does not declare exactly the framework packages: "
                f"{sorted(declared)}"
            )


def _verify_tracked_sources(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    *,
    source_root: Path,
    purelib: str,
) -> None:
    tracked = _tracked_files(source_root)
    expected = _expected_packaged_sources(source_root, tracked=tracked, purelib=purelib)

    for source_relative, archive_name in expected.items():
        _required_member(members, archive_name, source_relative.as_posix())
        source_digest = hashlib.sha256((source_root / source_relative).read_bytes()).digest()
        archive_digest = hashlib.sha256(archive.read(archive_name)).digest()
        if archive_digest != source_digest:
            _fail(f"Wheel source digest mismatch for {source_relative.as_posix()}")


def _expected_packaged_sources(
    source_root: Path,
    *,
    tracked: tuple[Path, ...],
    purelib: str,
) -> dict[Path, str]:
    expected: dict[Path, str] = {}
    for source_prefix, package_prefix in _PACKAGE_SOURCE_ROOTS.items():
        for relative in tracked:
            if not relative.is_relative_to(source_prefix) or _excluded(relative):
                continue
            source = source_root / relative
            if source.is_file():
                suffix = relative.relative_to(source_prefix).as_posix()
                expected[relative] = f"{purelib}/{package_prefix.as_posix()}/{suffix}"

    sdk_root = Path("sdk/vs-bench")
    for relative in tracked:
        if not relative.is_relative_to(sdk_root) or _excluded(relative):
            continue
        sdk_relative = relative.relative_to(sdk_root)
        if sdk_relative.parts[0] not in {"README.md", "pyproject.toml", "src"}:
            continue
        source = source_root / relative
        if source.is_file():
            expected[relative] = f"{purelib}/vibesys/_sdk/vs-bench/{sdk_relative.as_posix()}"
    return expected


def _tracked_files(source_root: Path) -> tuple[Path, ...]:
    git = shutil.which("git")
    if git is None:
        raise ReleaseWheelError.untracked_source_root(source_root)
    try:
        result = subprocess.run(  # noqa: S603
            [git, "-C", str(source_root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseWheelError.untracked_source_root(source_root) from exc
    return tuple(Path(value.decode()) for value in result.stdout.split(b"\0") if value)


def _excluded(path: Path) -> bool:
    return bool(_EXCLUDED_PARTS.intersection(path.parts)) or path.name.endswith(
        (".pyc", ".egg-info")
    )


def _verify_entry_points(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    *,
    dist_info: str,
) -> None:
    name = f"{dist_info}/entry_points.txt"
    _required_member(members, name, "console scripts")
    section: str | None = None
    actual: dict[str, str] = {}
    for raw_line in archive.read(name).decode().splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "console_scripts" and "=" in line:
            key, value = line.split("=", maxsplit=1)
            actual[key.strip()] = value.strip()
    if actual != _EXPECTED_ENTRY_POINTS:
        _fail(f"Wheel console scripts are incomplete or unexpected: {actual}")


def _verify_tui_payload(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    *,
    purelib: str,
    target: WheelTarget,
    version: str,
) -> None:
    root = f"{purelib}/vibesys/_tui"
    for relative in _REQUIRED_TUI_FILES:
        _required_member(members, f"{root}/{relative}", relative)

    runtime_info = members[f"{root}/bin/bun"]
    if not (runtime_info.external_attr >> 16) & 0o111:
        _fail("Bundled Bun runtime is not executable")

    manifest = _load_manifest(archive.read(f"{root}/manifest.json"))
    if manifest.get("schema_version") != 1:
        _fail("Unsupported TUI payload manifest schema")
    if manifest.get("target") != target.key:
        _fail(f"TUI payload target {manifest.get('target')!r} does not match {target.key!r}")
    if manifest.get("bun_version") != BUN_VERSION:
        _fail(f"TUI payload must use Bun version {BUN_VERSION}")
    if manifest.get("tui_version") != TUI_VERSION or version != TUI_VERSION:
        _fail("TUI payload version does not match the Python distribution version")
    _verify_manifest_hashes(archive, members, root=root, manifest=manifest)
    _verify_native_package(members, root=root, target=target)


def _load_manifest(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReleaseWheelError.invalid_manifest() from exc
    if not isinstance(value, dict):
        _fail("TUI payload manifest.json must contain an object")
    return cast("dict[str, object]", value)


def _verify_manifest_hashes(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    *,
    root: str,
    manifest: dict[str, object],
) -> None:
    raw_hashes = manifest.get("files")
    if not isinstance(raw_hashes, dict) or not all(
        isinstance(path, str) and isinstance(digest, str) for path, digest in raw_hashes.items()
    ):
        _fail("TUI payload manifest has invalid file hashes")
    expected_hashes = cast("dict[str, str]", raw_hashes)
    prefix = f"{root}/"
    actual = {
        name.removeprefix(prefix)
        for name in members
        if name.startswith(prefix) and name != f"{root}/manifest.json" and not name.endswith("/")
    }
    if set(expected_hashes) != actual:
        _fail("TUI payload manifest file list does not match its contents")
    for relative, digest in expected_hashes.items():
        actual_digest = hashlib.sha256(archive.read(f"{root}/{relative}")).hexdigest()
        if actual_digest != digest:
            _fail(f"TUI payload hash mismatch for {relative}")


def _verify_native_package(
    members: dict[str, zipfile.ZipInfo],
    *,
    root: str,
    target: WheelTarget,
) -> None:
    prefix = f"{root}/app/node_modules/@opentui/"
    native_packages = {
        f"@opentui/{remainder.split('/', maxsplit=1)[0]}"
        for name in members
        if name.startswith(prefix)
        for remainder in [name.removeprefix(prefix)]
        if remainder.startswith("core-") and "/" in remainder
    }
    if native_packages != {target.opentui_package}:
        _fail(
            "TUI payload must contain exactly its target OpenTUI native package; "
            f"found {sorted(native_packages)}"
        )


def _required_member(
    members: dict[str, zipfile.ZipInfo],
    name: str,
    description: str,
) -> None:
    if name not in members:
        _fail(f"Wheel is missing {description}: {name}")


def _fail(message: str) -> Never:
    raise ReleaseWheelError(message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--source-root", type=Path, default=REPO_ROOT)
    return parser.parse_args()


def main() -> int:
    """Verify the requested wheel and report a concise result."""
    args = _parse_args()
    try:
        verify_wheel(args.wheel, args.source_root, TARGETS[args.target])
    except ReleaseWheelError as exc:
        print(f"release wheel verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"verified {args.wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
