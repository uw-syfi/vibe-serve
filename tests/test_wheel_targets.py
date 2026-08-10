"""Contracts for the native release-wheel target matrix."""

from __future__ import annotations

import pytest
from wheel_targets import (  # pyright: ignore[reportMissingImports]
    TARGETS,
    WheelTargetError,
    resolve_wheel_target,
)

EXPECTED_PLATFORMS = {
    "linux-x86_64": "manylinux_2_28_x86_64",
    "linux-aarch64": "manylinux_2_28_aarch64",
    "macos-x86_64": "macosx_13_0_x86_64",
    "macos-arm64": "macosx_13_0_arm64",
}


@pytest.mark.parametrize("key", EXPECTED_PLATFORMS)
def test_supported_target_round_trip(key):  # noqa: ANN001, ANN201
    target = TARGETS[key]

    resolved = resolve_wheel_target(
        key,
        host_system=target.system,
        host_machine=target.machine,
    )

    assert resolved == target
    assert resolved.wheel_platform == EXPECTED_PLATFORMS[key]
    assert resolved.opentui_package.startswith("@opentui/core-")
    assert resolved.bun_asset.endswith(".zip")


def test_target_resolution_rejects_an_unknown_target():  # noqa: ANN201
    with pytest.raises(WheelTargetError, match="Unsupported wheel target"):
        resolve_wheel_target("windows-x86_64", host_system="Windows", host_machine="AMD64")


def test_target_resolution_rejects_cross_compilation():  # noqa: ANN201
    with pytest.raises(WheelTargetError, match="must be built natively"):
        resolve_wheel_target(
            "linux-aarch64",
            host_system="Linux",
            host_machine="x86_64",
        )
