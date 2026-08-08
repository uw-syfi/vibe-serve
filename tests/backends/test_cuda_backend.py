from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibesys.backends import SandboxKind
from vibesys.backends.cuda import CudaBackend


def test_cpu_only_control_plane_docker_skips_gpu_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = CudaBackend(tmp_path)
    pick_device = MagicMock()
    monkeypatch.setattr(backend, "_pick_device", pick_device)

    sandbox = backend.make_sandbox(
        SandboxKind.DOCKER,
        host_workspace=str(tmp_path),
        log_path=None,
        attach_accelerator=False,
    )

    pick_device.assert_not_called()
    assert sandbox._gpus is None  # noqa: SLF001  # tracked: #288
    assert backend.selected_device is None
