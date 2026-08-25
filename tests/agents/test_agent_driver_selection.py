"""Tests for selecting an agent driver through application configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from vibesys.agents import build_agent_client
from vibesys.agents.drivers.agentshim import AgentShimDriver
from vibesys.agents.drivers.omnigent import OmnigentDriver, OmnigentDriverError
from vibesys.agents.omnigent import supported_providers
from vibesys.agents.omnigent.providers import OMNIGENT_PROVIDER_EXECUTORS
from vibesys.config import Config
from vs_sandbox import HostResource, HostResourceAccess

if TYPE_CHECKING:
    from vibesys.agents.client import AgentClient


def _config(**agent: object) -> Config:
    return Config.model_validate({"model": {"name": "m"}, "agent": agent})


def _build(config: Config, **overrides: object) -> AgentClient:
    kwargs: dict[str, object] = {
        "agent_backend": None,
        "cli_provider": None,
        "backends": None,
        "skills": [],
        "skill_source_dirs": [],
        "model": None,
        "model_name": "m",
        "run_log_file": None,
        "use_docker": False,
    }
    kwargs.update(overrides)
    return build_agent_client(config, **kwargs)  # pyright: ignore[reportArgumentType]


def test_agentshim_is_the_default_driver() -> None:
    client = _build(_config(backend="cli", cli_provider="codex"))

    assert isinstance(client._driver, AgentShimDriver)  # noqa: SLF001


@pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
def test_default_driver_supports_all_agentshim_providers(provider: str) -> None:
    client = _build(_config(backend="cli", cli_provider=provider))

    assert isinstance(client._driver, AgentShimDriver)  # noqa: SLF001
    assert client._provider == provider  # noqa: SLF001


def test_agentshim_docker_configuration_is_preserved() -> None:
    backends = {"implementer": MagicMock(), "judge": MagicMock(), "perf_eval": MagicMock()}

    client = _build(
        _config(backend="cli", cli_provider="claude"),
        backends=backends,
        use_docker=True,
    )

    assert client._driver._docker_sandboxes is backends  # noqa: SLF001


def test_omnigent_driver_can_be_selected() -> None:
    client = _build(_config(driver="omnigent", backend="cli", cli_provider="claude"))

    assert isinstance(client._driver, OmnigentDriver)  # noqa: SLF001


def test_omnigent_selection_passes_model_and_log_dir(tmp_path) -> None:  # noqa: ANN001
    client = _build(
        _config(driver="omnigent", backend="cli", cli_provider="codex"),
        model_name="gpt-5",
        log_dir=tmp_path,
    )

    assert client._model_name == "gpt-5"  # noqa: SLF001
    assert client._log_dir == tmp_path  # noqa: SLF001


def test_driver_is_rejected_for_non_cli_backend() -> None:
    with pytest.raises(SystemExit, match="valid only"):
        _build(_config(driver="omnigent", backend="deepagents"), backends={})


@pytest.mark.parametrize("provider", ["gemini", "opencode"])
def test_omnigent_rejects_unsupported_provider_with_remedy(provider: str) -> None:
    with pytest.raises(OmnigentDriverError) as exc:
        _build(_config(driver="omnigent", backend="cli", cli_provider=provider))

    message = str(exc.value)
    assert provider in message
    assert "claude" in message
    assert "codex" in message
    assert "agentshim" in message


def test_omnigent_rejects_docker() -> None:
    with pytest.raises(SystemExit, match="--docker"):
        _build(
            _config(driver="omnigent", backend="cli", cli_provider="claude"),
            backends={"implementer": MagicMock()},
            use_docker=True,
        )


def test_omnigent_rejects_host_resource_grants(tmp_path) -> None:  # noqa: ANN001
    grant = HostResource(tmp_path / "models", HostResourceAccess.READ_ONLY, "weights")

    with pytest.raises(OmnigentDriverError) as exc:
        _build(
            _config(driver="omnigent", backend="cli", cli_provider="claude"),
            host_resources=[grant],
        )

    message = str(exc.value)
    assert "models" in message
    assert "agentshim" in message


def test_omnigent_accepts_empty_host_resources() -> None:
    client = _build(
        _config(driver="omnigent", backend="cli", cli_provider="claude"),
        host_resources=(),
    )

    assert isinstance(client._driver, OmnigentDriver)  # noqa: SLF001


def test_omnigent_provider_registry_matches_supported_providers() -> None:
    assert supported_providers() == ["claude", "codex"]
    assert set(OMNIGENT_PROVIDER_EXECUTORS) == set(supported_providers())


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_omnigent_specs_identify_inner_executors(provider: str) -> None:
    spec = OMNIGENT_PROVIDER_EXECUTORS[provider]

    assert spec.module.startswith("omnigent.inner.")
    assert spec.class_name.endswith("Executor")
    assert spec.harness
