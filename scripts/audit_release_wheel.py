"""Audit every embedded ELF or Mach-O in one native release wheel."""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Never

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wheel_targets import TARGETS, WheelTarget, resolve_wheel_target  # noqa: E402

Runner = Callable[..., subprocess.CompletedProcess[str]]
_GLIBC_MAX = (2, 28)
_GLIBC_PATTERN = re.compile(r"\bGLIBC_(\d+)\.(\d+)(?:\.(\d+))?\b")
_MACHO_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)
_LINUX_MACHINES = {
    "linux-x86_64": "Advanced Micro Devices X86-64",
    "linux-aarch64": "AArch64",
}
_MACOS_ARCHES = {"macos-x86_64": "x86_64", "macos-arm64": "arm64"}
_MANYLINUX_LIBRARIES = frozenset(
    {
        "ld-linux-aarch64.so.1",
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libcrypt.so.1",
        "libdl.so.2",
        "libgcc_s.so.1",
        "libm.so.6",
        "libnsl.so.1",
        "libpthread.so.0",
        "libresolv.so.2",
        "librt.so.1",
        "libstdc++.so.6",
        "libutil.so.1",
    }
)


class BinaryAuditError(RuntimeError):
    """Raised when an embedded native binary violates compatibility policy."""

    @classmethod
    def unreadable_wheel(cls, wheel: Path, cause: object) -> BinaryAuditError:
        """Build an error for a wheel that cannot be inspected."""
        return cls(f"Cannot audit wheel {wheel}: {cause}")

    @classmethod
    def command_failed(cls, command: Sequence[str]) -> BinaryAuditError:
        """Build an error for a failed native inspection command."""
        return cls(f"Native audit command failed: {' '.join(command)}")


def parse_readelf_machine(output: str) -> str:
    """Extract the ELF machine value from ``readelf -h`` output."""
    match = re.search(r"^\s*Machine:\s*(.+?)\s*$", output, re.MULTILINE)
    if match is None:
        _fail("readelf output does not declare Machine")
    return match.group(1)


def parse_readelf_needed(output: str) -> set[str]:
    """Extract DT_NEEDED library basenames from ``readelf -d`` output."""
    return set(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", output))


def parse_glibc_versions(output: str) -> set[tuple[int, ...]]:
    """Extract all referenced GLIBC symbol versions."""
    return {
        tuple(int(value) for value in match.groups() if value is not None)
        for match in _GLIBC_PATTERN.finditer(output)
    }


def validate_glibc_versions(versions: set[tuple[int, ...]]) -> None:
    """Reject symbol versions newer than manylinux_2_28."""
    too_new = sorted(version for version in versions if version > _GLIBC_MAX)
    if too_new:
        rendered = ", ".join("GLIBC_" + ".".join(map(str, version)) for version in too_new)
        _fail(f"ELF references GLIBC versions newer than 2.28: {rendered}")


def validate_linux_libraries(libraries: set[str]) -> None:
    """Reject DT_NEEDED entries outside the manylinux-compatible system set."""
    unexpected = sorted(libraries - _MANYLINUX_LIBRARIES)
    if unexpected:
        _fail(f"ELF depends on non-manylinux system libraries: {unexpected}")


def parse_lipo_architectures(output: str) -> set[str]:
    """Extract architectures from either thin or universal ``lipo -archs`` output."""
    marker = " are: " if " are: " in output else " architecture: "
    if marker not in output:
        _fail("lipo output does not declare architectures")
    return set(output.strip().split(marker, maxsplit=1)[1].split())


def parse_macos_deployment_targets(output: str) -> set[tuple[int, ...]]:
    """Extract minimum macOS versions from ``otool -l`` output."""
    versions: set[tuple[int, ...]] = set()
    command: str | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("cmd "):
            command = line.removeprefix("cmd ")
            continue
        field = "minos" if command == "LC_BUILD_VERSION" else "version"
        if command not in {"LC_BUILD_VERSION", "LC_VERSION_MIN_MACOSX"}:
            continue
        match = re.fullmatch(rf"{field}\s+(\d+(?:\.\d+)+)", line)
        if match is not None:
            versions.add(tuple(int(value) for value in match.group(1).split(".")))
    return versions


def validate_macos_deployment_targets(versions: set[tuple[int, ...]]) -> None:
    """Reject binaries requiring a macOS version newer than 13."""
    if not versions:
        _fail("Mach-O does not declare a minimum macOS deployment target")
    too_new = sorted(version for version in versions if version > (13, 0))
    if too_new:
        rendered = ", ".join(".".join(map(str, version)) for version in too_new)
        _fail(f"Mach-O requires macOS newer than 13.0: {rendered}")


def audit_wheel(
    wheel: Path,
    target: WheelTarget,
    *,
    runner: Runner = subprocess.run,
) -> None:
    """Discover and audit every native binary in ``wheel`` on its target host."""
    resolve_wheel_target(
        target.key,
        host_system=platform.system(),
        host_machine=platform.machine(),
    )
    expected_kind = "elf" if target.system == "Linux" else "macho"
    discovered = 0
    try:
        with (
            zipfile.ZipFile(wheel.resolve()) as archive,
            tempfile.TemporaryDirectory(prefix=f"vibesys-audit-{target.key}-") as temporary,
        ):
            for index, info in enumerate(archive.infolist()):
                if info.is_dir():
                    continue
                content = archive.read(info)
                kind = _binary_kind(content)
                if kind is None:
                    continue
                if kind != expected_kind:
                    _fail(f"Wheel contains unexpected {kind} binary: {info.filename}")
                discovered += 1
                extracted = Path(temporary) / f"native-{index}"
                extracted.write_bytes(content)
                if kind == "elf":
                    _audit_elf(extracted, info.filename, target=target, runner=runner)
                else:
                    _audit_macho(extracted, info.filename, target=target, runner=runner)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BinaryAuditError.unreadable_wheel(wheel, exc) from exc
    if discovered == 0:
        _fail(f"Wheel contains no {expected_kind.upper()} binaries")


def _binary_kind(content: bytes) -> str | None:
    if content.startswith(b"\x7fELF"):
        return "elf"
    if content[:4] in _MACHO_MAGICS:
        return "macho"
    return None


def _audit_elf(path: Path, member: str, *, target: WheelTarget, runner: Runner) -> None:
    machine = parse_readelf_machine(_run(["readelf", "-W", "-h", str(path)], runner=runner))
    expected = _LINUX_MACHINES[target.key]
    if machine != expected:
        _fail(f"ELF {member} has machine {machine!r}, expected {expected!r}")
    dynamic = _run(["readelf", "-W", "-d", str(path)], runner=runner)
    validate_linux_libraries(parse_readelf_needed(dynamic))
    versions = _run(["readelf", "-W", "--version-info", str(path)], runner=runner)
    validate_glibc_versions(parse_glibc_versions(versions))


def _audit_macho(path: Path, member: str, *, target: WheelTarget, runner: Runner) -> None:
    architectures = parse_lipo_architectures(_run(["lipo", "-archs", str(path)], runner=runner))
    expected = {_MACOS_ARCHES[target.key]}
    if architectures != expected:
        _fail(
            f"Mach-O {member} has architectures {sorted(architectures)}, expected {sorted(expected)}"
        )
    load_commands = _run(["otool", "-l", str(path)], runner=runner)
    validate_macos_deployment_targets(parse_macos_deployment_targets(load_commands))


def _run(command: Sequence[str], *, runner: Runner) -> str:
    try:
        result = runner(list(command), check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BinaryAuditError.command_failed(command) from exc
    return result.stdout


def _fail(message: str) -> Never:
    raise BinaryAuditError(message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    return parser.parse_args()


def main() -> int:
    """Audit the requested wheel with concise diagnostics."""
    args = _parse_args()
    try:
        audit_wheel(args.wheel, TARGETS[args.target])
    except (BinaryAuditError, ValueError) as exc:
        print(f"native binary audit failed: {exc}", file=sys.stderr)
        return 1
    print(f"audited {args.wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
