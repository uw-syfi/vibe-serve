"""ROCm backend: AMD Instinct GPUs + ROCm PyTorch container + torch profiler.

ROCm shares the discrete-accelerator model with CUDA — separate device
memory, dynamic shapes, per-kernel launch cost — so the serving techniques
carry over. What differs is plumbing:

* AMD GPUs are exposed as ``/dev/kfd`` (the compute driver) plus
  ``/dev/dri/*`` render nodes, forwarded with ``docker --device`` rather
  than the NVIDIA-only ``--gpus`` flag. Container users additionally need
  the ``video`` and ``render`` groups.
* The runtime ships in the ROCm PyTorch image (``rocm/pytorch``).
* Device selection uses ``HIP_VISIBLE_DEVICES`` and ``rocm-smi``.
* Profiling uses ``torch.profiler``, which works unmodified on ROCm.
  ``rocprofv3`` / ``omniperf`` are the system- and kernel-altitude tools
  but are not wired as a dedicated :class:`ProfilerKind` yet.

.. warning::

   **Experimental.** This backend is wired end to end but has not been
   exercised against MI300-class hardware in this repository. Device
   discovery and the container contract follow the documented ROCm
   conventions; treat them as unverified until a run confirms them. Like
   :class:`~vibesys.backends.local.LocalBackend`'s ``metal`` and ``cpu``
   bindings, the curriculum is not wired up — the simple loop is the
   intended entry point.

Modal offers no AMD GPUs, so ``make_sandbox`` raises on
``SandboxKind.MODAL`` (parity with the Trainium backend).
"""

from __future__ import annotations

import glob
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from vibesys.backends.base import (
    ContentionMonitor,
    ModalOptions,
    SandboxKind,
    SetupFn,
)
from vibesys.constants import ComputeBackend
from vibesys.profilers import ProfilerKind
from vs_sandbox import DockerSandbox

# ROCm PyTorch image. Carries the ROCm runtime + a matching torch build.
# Override with ``--docker-image`` when the host ROCm version differs —
# the container's ROCm must be compatible with the host kernel driver.
_DEFAULT_IMAGE = "rocm/pytorch:latest"

# The compute driver node, required for any HIP program.
_KFD_DEVICE = "/dev/kfd"

# Docker's default 64 MB /dev/shm is too small for multi-GPU collectives
# (RCCL) and large-model loading.
_DEFAULT_SHM_SIZE = "16g"

# Container groups needed to open /dev/kfd and /dev/dri nodes.
_DEVICE_GROUPS: tuple[str, ...] = ("video", "render")


def _discover_rocm_devices() -> list[str]:
    """Return the host's AMD GPU device nodes, sorted.

    ``/dev/kfd`` is the compute driver and is required; ``/dev/dri/render*``
    nodes are the per-GPU render devices. Returns an empty list when the
    host has no AMD GPU, in which case the container starts without an
    accelerator (parity with the Trainium backend's behaviour).
    """
    if not os.path.exists(_KFD_DEVICE):
        return []
    render_nodes = sorted(glob.glob("/dev/dri/render*"))
    return [_KFD_DEVICE, *render_nodes]


def _query_rocm_gpu_count() -> int | None:
    """Return the number of GPUs ``rocm-smi`` reports, or None if unavailable."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showid", "--csv"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    # CSV: header row then one row per device.
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    return max(len(rows) - 1, 0)


class RocmBackend:
    """AMD ROCm backend (local or Docker; no Modal).

    Experimental — see the module docstring.
    """

    name = ComputeBackend.ROCM
    profiler_kind = ProfilerKind.TORCH

    def __init__(
        self,
        log_dir: Path,
        *,
        log: Callable[[str], None] | None = None,
        image: str | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self._lprint = log or print
        self.image = image or _DEFAULT_IMAGE
        # No per-device auto-selection yet; pinning is via HIP_VISIBLE_DEVICES.
        # Kept for ComputeBackendImpl parity.
        self.selected_device = None
        self._devices = _discover_rocm_devices()

        if self._devices:
            count = _query_rocm_gpu_count()
            detail = f"{count} GPU(s), " if count is not None else ""
            self._lprint(
                f"[rocm] {detail}forwarding {len(self._devices)} device node(s): "
                f"{', '.join(self._devices)}"
            )
        else:
            self._lprint(
                "[rocm] No /dev/kfd found on host — the container will start "
                "without an accelerator."
            )

    # -- ComputeBackendImpl protocol ---------------------------------------

    def make_sandbox(
        self,
        kind: SandboxKind,
        *,
        host_workspace: str,
        log_path: Path | str | None,
        bind_mounts: list[tuple[str, str, bool]] | None = None,
        passthrough_paths: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        extra_init_commands: list[str] | None = None,
        setup_fns: list[SetupFn] | None = None,
        modal_options: ModalOptions | None = None,
    ) -> SandboxBackendProtocol:
        bind_mounts = list(bind_mounts or [])
        passthrough_paths = list(passthrough_paths or [])
        extra_env = dict(extra_env or {})
        extra_init_commands = list(extra_init_commands or [])
        setup_fns = setup_fns or []

        if kind is SandboxKind.MODAL:
            raise ValueError(
                "rocm backend does not support Modal — Modal offers no AMD "
                "GPUs. Use --docker (Instinct GPUs via /dev/kfd) or local "
                "execution."
            )

        env = self._build_env(extra_env)

        if kind is SandboxKind.LOCAL:
            return LocalShellBackend(
                root_dir=host_workspace,
                virtual_mode=True,
                inherit_env=True,
                env=env,
            )

        if kind is SandboxKind.DOCKER:
            return DockerSandbox(
                host_workspace=host_workspace,
                image=self.image,
                gpus=None,  # ROCm uses --device, not --gpus
                devices=self._devices,
                group_add=list(_DEVICE_GROUPS),
                shm_size=_DEFAULT_SHM_SIZE,
                bind_mounts=bind_mounts,
                passthrough_paths=passthrough_paths,
                env=env,
                log_path=log_path,
                extra_init_commands=extra_init_commands,
                setup_fns=setup_fns,
            )

        raise ValueError(f"Unknown sandbox kind: {kind!r}")

    def make_monitor(self, log_dir: Path) -> ContentionMonitor | None:
        # rocm-smi can report utilization, but shared-device contention
        # handling isn't wired up yet; skip rather than fake it.
        return None

    def reselect_device(self) -> None:
        return None

    # -- internal ----------------------------------------------------------

    def _build_env(self, extra: dict[str, str]) -> dict[str, str]:
        """ROCm runtime env, with caller extras taking precedence."""
        env: dict[str, str] = {}
        # Respect an operator-pinned device selection; otherwise leave the
        # runtime to enumerate every forwarded GPU.
        visible = os.environ.get("HIP_VISIBLE_DEVICES")
        if visible:
            env["HIP_VISIBLE_DEVICES"] = visible
        env.update(extra)
        return env
