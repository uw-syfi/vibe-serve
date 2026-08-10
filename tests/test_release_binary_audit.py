"""Unit-testable native binary compatibility policy."""

from __future__ import annotations

import pytest
from scripts.audit_release_wheel import (  # pyright: ignore[reportMissingImports]
    BinaryAuditError,
    parse_glibc_versions,
    parse_lipo_architectures,
    parse_macos_deployment_targets,
    parse_readelf_machine,
    parse_readelf_needed,
    validate_glibc_versions,
    validate_linux_libraries,
    validate_macos_deployment_targets,
)

READELF_HEADER_X64 = """\
ELF Header:
  Class:                             ELF64
  Machine:                           Advanced Micro Devices X86-64
"""

READELF_DYNAMIC = """\
Dynamic section at offset 0x2d90 contains 27 entries:
 0x0000000000000001 (NEEDED)             Shared library: [libm.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
"""

READELF_VERSIONS = """\
  004:   2 (GLIBC_2.2.5)   3 (GLIBC_2.17)    4 (GLIBC_2.28)
  0x0010:   Name: GLIBC_2.25  Flags: none  Version: 5
"""

OTOOL_LOAD_COMMANDS = """\
Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 13.0
      sdk 15.4
Load command 9
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 12.6
      sdk 13.3
Load command 10
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 99.0
"""


def test_parse_linux_native_tool_output() -> None:
    assert parse_readelf_machine(READELF_HEADER_X64) == "Advanced Micro Devices X86-64"
    assert parse_readelf_needed(READELF_DYNAMIC) == {"libm.so.6", "libc.so.6"}
    assert parse_glibc_versions(READELF_VERSIONS) == {(2, 2, 5), (2, 17), (2, 25), (2, 28)}


def test_linux_audit_rejects_new_glibc_or_non_system_dependencies() -> None:
    with pytest.raises(BinaryAuditError, match=r"GLIBC_2\.29"):
        validate_glibc_versions({(2, 17), (2, 29)})
    with pytest.raises(BinaryAuditError, match=r"libssl\.so\.3"):
        validate_linux_libraries({"libc.so.6", "libssl.so.3"})


def test_parse_macos_native_tool_output() -> None:
    assert parse_lipo_architectures("Architectures in the fat file: bun are: x86_64 arm64\n") == {
        "x86_64",
        "arm64",
    }
    assert parse_lipo_architectures("Non-fat file: bun is architecture: arm64\n") == {"arm64"}
    assert parse_macos_deployment_targets(OTOOL_LOAD_COMMANDS) == {(13, 0), (12, 6)}


def test_macos_audit_rejects_deployment_targets_above_macos_13() -> None:
    validate_macos_deployment_targets({(12, 0), (13, 0)})
    with pytest.raises(BinaryAuditError, match=r"14\.0"):
        validate_macos_deployment_targets({(13, 0), (14, 0)})
