"""Tests for the Python backend entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from vibesys.domains.base import DomainName
from vibesys.errors import ConfigurationDiagnostic, ConfigurationError
from vibesys.main import (
    _control_socket_from_argv,
    _detect_resume_round,
    _extract_flag,
    _extract_loop_selection,
    _infer_resume_input,
    _load_objectives_toml,
    _load_pareto_relative_noise_toml,
    _parse_cli_objective,
    _prepare_experiment_repository,
    _prepare_stub_agent_smoke_defaults,
    _prune_rounds_state,
    _render_configuration_error,
    _resolve_run_dir,
    _validate_target_inputs,
    _with_operator_constraints,
    load_config_and_skills,
    main,
    parse_cli_invocation,
)
from vibesys.profilers import ProfilerKind
from vs_github import GitHubCLI


def _patch_loop_runner(loop_name: str, runner: Mock):  # noqa: ANN202  # tracked: #288
    """Swap the dispatch entry's ``run`` function for *runner*.

    ``_LOOP_COMMANDS`` holds direct function references, so patching the
    module-level function name no longer affects dispatch — patch the
    command record instead.
    """
    import dataclasses  # noqa: PLC0415  # tracked: #288

    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    command = cli._LOOP_COMMANDS[loop_name]  # noqa: SLF001  # tracked: #288
    patched = dataclasses.replace(command, run=runner)
    return patch.dict(cli._LOOP_COMMANDS, {loop_name: patched})  # noqa: SLF001  # tracked: #288


TARGET_INPUT_ARGS = ["--input", "examples/model-serving/Llama-3-8B"]
TARGET_ARGS = [*TARGET_INPUT_ARGS, "--runs-dir", "/tmp/vibesys-test-runs"]  # noqa: S108


def _write_input_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "queue-spsc"
    (bundle / "reference").mkdir(parents=True)
    (bundle / "OBJECTIVE.md").write_text("objective\n")
    (bundle / "vibesys.input.toml").write_text(
        """
version = 1

[agent]
domain = "generic"

[accuracy]
command = ["uv", "run", "python", "accuracy_checker/checker.py"]

[benchmark]
command = ["uv", "run", "python", "benchmark/benchmark.py"]
""".lstrip()
    )
    return bundle


def _write_resume_event(
    exp_dir: Path, input_path: Path, *, event_type: str = "run_started"
) -> None:
    logs = exp_dir / "logs"
    logs.mkdir(parents=True)
    content = (
        '{"type": "server_started", "data": null}\n'
        f'{{"type": "{event_type}", "data": '
        f'{{"kind": "run_started", "outer_loop": "agent", "input": "{input_path}", '
        '"max_rounds": 2}}\n'
    )
    (logs / "run-events.jsonl").write_text(content)


# ---------------------------------------------------------------------------
# Flag extraction
# ---------------------------------------------------------------------------


def test_extract_flag_space_form():  # noqa: ANN201  # tracked: #288
    val, rest = _extract_flag(["--outer-loop", "agent", "--input", "x"], "--outer-loop")
    assert val == "agent"
    assert rest == ["--input", "x"]


def test_extract_flag_equals_form():  # noqa: ANN201  # tracked: #288
    val, rest = _extract_flag(["--input", "x", "--outer-loop=evolve"], "--outer-loop")
    assert val == "evolve"
    assert rest == ["--input", "x"]


def test_extract_flag_missing_returns_none():  # noqa: ANN201  # tracked: #288
    val, rest = _extract_flag(["--input", "x"], "--outer-loop")
    assert val is None
    assert rest == ["--input", "x"]


def test_extract_flag_dangling_exits():  # noqa: ANN201  # tracked: #288
    with pytest.raises(ConfigurationError) as exc:
        _extract_flag(["--outer-loop"], "--outer-loop")
    assert exc.value.diagnostic.code == "invalid_arguments"


# ---------------------------------------------------------------------------
# argv -> loop kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected_kind,expected_rest",  # noqa: PT006  # tracked: #288
    [
        (["--outer-loop", "agent", "--input", "x"], "agent", ["--input", "x"]),
        (["--outer-loop", "plain", "--exp-name", "e"], "plain", ["--exp-name", "e"]),
        (["--outer-loop", "evolve", "--seed", "1"], "evolve", ["--seed", "1"]),
    ],
)
def test_extract_loop_selection(argv: list[str], expected_kind: str, expected_rest: list[str]):  # noqa: ANN201  # tracked: #288
    kind, rest = _extract_loop_selection(argv)
    assert kind == expected_kind
    assert rest == expected_rest


def test_extract_loop_selection_defaults_to_agent():  # noqa: ANN201  # tracked: #288
    kind, rest = _extract_loop_selection(["--input", "x"])
    assert kind == "agent"
    assert rest == ["--input", "x"]


def test_extract_loop_selection_unknown_outer_loop_exits():  # noqa: ANN201  # tracked: #288
    with pytest.raises(ConfigurationError) as exc:
        _extract_loop_selection(["--outer-loop", "nope"])
    assert exc.value.diagnostic.stage == "argument_parsing"


def test_target_input_defaults_to_none():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    args = _build_agent_parser().parse_args([])

    assert args.input is None
    assert not hasattr(args, "ref")
    assert not hasattr(args, "acc_checker")
    assert not hasattr(args, "bench")
    assert args.profiler is ProfilerKind.AUTO
    assert not hasattr(args, "profiler_support")
    assert not hasattr(args, "domain")
    # Standalone-input flags default to None (they synthesize a bundle when set).
    assert args.input_domain is None
    assert args.input_objective is None


def test_run_invocation_requires_runs_dir() -> None:
    with pytest.raises(ConfigurationError) as exc:
        parse_cli_invocation(["--outer-loop", "agent", *TARGET_INPUT_ARGS])

    assert exc.value.diagnostic.code == "missing_runs_dir"
    assert "--runs-dir PATH is required" in exc.value.diagnostic.message


@pytest.mark.parametrize("value", ["", " \t "])
def test_run_parser_rejects_an_empty_runs_directory(value: str) -> None:
    with pytest.raises(ConfigurationError) as exc:
        parse_cli_invocation([f"--runs-dir={value}", *TARGET_INPUT_ARGS])

    assert exc.value.diagnostic.code == "invalid_arguments"
    assert exc.value.diagnostic.stage == "argument_parsing"
    assert "--runs-dir" in exc.value.diagnostic.message


def test_runs_dir_is_normalized_to_an_absolute_collection_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _write_input_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)

    invocation = parse_cli_invocation(
        ["--outer-loop", "agent", "--runs-dir", "runs", "--input", str(bundle)]
    )

    assert invocation.args.runs_dir == (tmp_path / "runs").resolve()


def test_runs_dir_rejects_python_installation_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    prefix = tmp_path / ".venv"
    monkeypatch.setattr(cli.sys, "prefix", str(prefix))

    with pytest.raises(ConfigurationError) as exc:
        parse_cli_invocation(
            ["--outer-loop", "agent", "--runs-dir", str(prefix / "runs"), *TARGET_INPUT_ARGS]
        )

    assert exc.value.diagnostic.code == "invalid_runs_dir"
    assert "Python installation prefix" in exc.value.diagnostic.message


def test_source_checkout_exp_env_outside_venv_prefix_is_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path / ".venv"))

    invocation = parse_cli_invocation(
        ["--outer-loop", "agent", "--runs-dir", str(tmp_path / "exp_env"), *TARGET_INPUT_ARGS]
    )

    assert invocation.args.runs_dir == (tmp_path / "exp_env").resolve()


@pytest.mark.parametrize(
    "obsolete_flag",
    ["--profiler-support", "--nsys-profiler", "--torch-profiler", "--neuron-profiler"],
)
def test_profiler_support_override_flags_are_rejected(obsolete_flag):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    with pytest.raises(ConfigurationError, match="unrecognized arguments"):
        _build_agent_parser().parse_args([obsolete_flag, "support"])


@pytest.mark.parametrize(
    "builder_name",
    [
        "_build_agent_parser",
        "_build_evolve_parser",
        "_build_plain_parser",
    ],
)
def test_input_arg_is_available_on_all_loop_parsers(builder_name):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    parser = getattr(cli, builder_name)()
    args = parser.parse_args(["--input", "examples/data-structures/queue-spsc"])

    assert args.input == Path("examples/data-structures/queue-spsc")


def test_remote_repository_options_are_user_configurable():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288
    from vibesys.run import RepositoryVisibility  # noqa: PLC0415  # tracked: #288

    args = _build_agent_parser().parse_args(
        ["--repo", "my-lab/trial", "--repo-visibility", "internal"]
    )

    assert args.repo == "my-lab/trial"
    assert args.repo_visibility is RepositoryVisibility.INTERNAL


def test_short_repository_name_uses_configured_owner(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288
    from vibesys.run import RepositoryVisibility  # noqa: PLC0415  # tracked: #288

    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """\
[model]
name = "gpt-5.5"

[repository]
owner = "my-playground"
visibility = "internal"
"""
    )
    args = _build_agent_parser().parse_args(
        ["--repo", "generated-trial", "--config", str(config_path), "--no-skills"]
    )

    load_config_and_skills(args, domain=DomainName.GENERIC)

    assert args.repo == "my-playground/generated-trial"
    assert args.repo_visibility is RepositoryVisibility.INTERNAL


def test_fresh_runs_default_to_authenticated_github_account(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_agent_parser().parse_args(["--input", str(bundle), "--no-skills"])  # noqa: SLF001  # tracked: #288
    cli._validate_target_inputs(args)  # noqa: SLF001  # tracked: #288
    config = cli.Config.model_validate({"model": {"name": "gpt-5.5"}})
    github = Mock()
    github.current_user.return_value = "octocat"
    monkeypatch.setattr(cli, "GitHubCLI", Mock(return_value=github))

    _prepare_experiment_repository(args, config)

    assert args.exp_name.startswith("queue-spsc-")
    assert args.repo == f"octocat/{args.exp_name}"
    github.current_user.assert_called_once_with()


def test_repository_owner_override_does_not_query_gh(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_agent_parser().parse_args(["--input", str(bundle), "--no-skills"])  # noqa: SLF001  # tracked: #288
    cli._validate_target_inputs(args)  # noqa: SLF001  # tracked: #288
    config = cli.Config.model_validate(
        {"model": {"name": "gpt-5.5"}, "repository": {"owner": "my-org"}}
    )
    github = Mock()
    monkeypatch.setattr(cli, "GitHubCLI", Mock(return_value=github))

    _prepare_experiment_repository(args, config)

    assert args.repo == f"my-org/{args.exp_name}"
    github.current_user.assert_not_called()


def test_local_runs_keep_generated_name_and_skip_github(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_agent_parser().parse_args(["--input", str(bundle), "--local", "--no-skills"])  # noqa: SLF001  # tracked: #288
    cli._validate_target_inputs(args)  # noqa: SLF001  # tracked: #288
    config = cli.Config.model_validate({"model": {"name": "gpt-5.5"}})
    github = Mock()
    monkeypatch.setattr(cli, "GitHubCLI", Mock(return_value=github))

    _prepare_experiment_repository(args, config)

    assert args.exp_name.startswith("queue-spsc-")
    assert args.repo is None
    github.current_user.assert_not_called()


def test_missing_gh_credentials_report_repository_setup_error(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    config = cli.Config.model_validate({"model": {"name": "gpt-5.5"}})
    github = Mock()
    github.current_user.side_effect = cli.GitHubCLIError("run gh auth login")
    monkeypatch.setattr(cli, "GitHubCLI", Mock(return_value=github))

    with pytest.raises(ConfigurationError) as exc:
        cli._resolve_repository_owner(config)  # noqa: SLF001  # tracked: #288

    assert exc.value.diagnostic.code == "repository_setup_failed"
    assert exc.value.diagnostic.stage == "repository_setup"
    assert "gh auth login" in exc.value.diagnostic.message


def test_local_and_repo_are_mutually_exclusive(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    config_path = tmp_path / "agent.toml"
    config_path.write_text('[model]\nname = "gpt-5.5"\n')
    args = _build_agent_parser().parse_args(
        [
            "--input",
            str(bundle),
            "--config",
            str(config_path),
            "--local",
            "--repo",
            "owner/name",
            "--no-skills",
        ]
    )

    with pytest.raises(ConfigurationError, match="--local cannot be combined"):
        load_config_and_skills(args, domain=DomainName.GENERIC)


@pytest.mark.parametrize(
    "builder_name,validator_name",  # noqa: PT006  # tracked: #288
    [
        ("_build_agent_parser", "_validate_agent"),
        ("_build_evolve_parser", "_validate_evolve"),
        ("_build_plain_parser", "_validate_plain"),
    ],
)
def test_profiler_none_is_valid_with_modal(builder_name, validator_name, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = getattr(cli, builder_name)()
    validator = getattr(cli, validator_name)
    args = parser.parse_args(["--modal", "--profiler", "none", "--input", str(bundle)])

    assert args.profiler is ProfilerKind.NONE
    validator(args)
    assert args.input_bundle.root == bundle.resolve()


def test_profiler_validation_uses_selected_environment_capabilities(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_agent_parser().parse_args(["--profiler", "nsys", "--input", str(bundle)])  # noqa: SLF001  # tracked: #288
    selected_specs = []

    def fake_environment(spec):  # noqa: ANN001, ANN202  # tracked: #288
        selected_specs.append(spec)
        return Mock(
            supported_profiler_kinds=frozenset(
                {ProfilerKind.AUTO, ProfilerKind.TORCH, ProfilerKind.NONE}
            )
        )

    monkeypatch.setattr(cli, "build_run_environment", fake_environment)

    with pytest.raises(ConfigurationError, match="run environment 'local'"):
        cli._validate_agent(args)  # noqa: SLF001  # tracked: #288
    assert [spec.name for spec in selected_specs] == ["local"]


def test_validate_evolve_rejects_nonpositive_bootstrap_attempts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """--bootstrap-max-attempts must be >= 1; 0 is a configuration error."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288
    args = parser.parse_args(["--bootstrap-max-attempts", "0", "--input", str(bundle)])
    assert args.bootstrap_max_attempts == 0
    with pytest.raises(ConfigurationError):
        cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288


def test_keep_deployments_flag_and_modal_alias_default_off_and_parse(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """The generic flag and compatibility alias select the same behavior."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288

    assert parser.parse_args(["--input", str(bundle)]).keep_deployments is False
    assert (
        parser.parse_args(["--keep-deployments", "--input", str(bundle)]).keep_deployments is True
    )
    assert parser.parse_args(["--keep-modal-apps", "--input", str(bundle)]).keep_deployments is True


def test_evolve_modality_defaults_to_domain_resolution(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """A non-serving domain must not inherit the text-generation modality."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288

    assert parser.parse_args(["--input", str(bundle)]).modality is None


def test_evolve_search_policy_defaults_and_openevolve_overrides(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288

    defaults = parser.parse_args(["--input", str(bundle)])
    assert defaults.search_policy is None
    assert defaults.openevolve_num_islands is None

    configured = parser.parse_args(
        [
            "--input",
            str(bundle),
            "--search-policy",
            "openevolve",
            "--openevolve-num-islands",
            "3",
            "--openevolve-population-size",
            "40",
            "--openevolve-archive-size",
            "20",
            "--openevolve-migration-interval",
            "4",
            "--openevolve-migration-rate",
            "0.25",
        ]
    )
    assert configured.search_policy == "openevolve"
    assert configured.openevolve_num_islands == 3
    assert configured.openevolve_population_size == 40
    assert configured.openevolve_archive_size == 20
    assert configured.openevolve_migration_interval == 4
    assert configured.openevolve_migration_rate == 0.25


def test_openevolve_partial_resume_options_merge_with_saved_config(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288
    from vibesys.loops.evolve.search_policy import (  # noqa: PLC0415  # tracked: #288
        OpenEvolveSearchConfig,
        OpenEvolveSearchPolicy,
    )

    bundle = _write_input_bundle(tmp_path)
    run_dir = tmp_path / "run"
    saved = OpenEvolveSearchConfig(
        population_size=40,
        archive_size=20,
        num_islands=3,
        migration_interval=4,
        migration_rate=0.25,
    )
    OpenEvolveSearchPolicy(
        state_dir=run_dir / "logs" / "openevolve",
        seed=1,
        config=saved,
    )
    args = cli._build_evolve_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--openevolve-migration-rate", "0.25"]
    )

    with patch.object(cli, "_resume_exp_dir", return_value=run_dir):
        policy_name, resolved = cli._resolve_openevolve_options(  # noqa: SLF001  # tracked: #288
            args,
            existing=True,
            exp_name="run",
            runs_dir=tmp_path,
        )

    assert policy_name == "openevolve"
    assert resolved == saved


def test_openevolve_resume_restores_flag_defined_objectives(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288
    from vibesys.loops.evolve.population import Objective  # noqa: PLC0415  # tracked: #288
    from vibesys.loops.evolve.search_policy import (  # noqa: PLC0415  # tracked: #288
        OpenEvolveSearchPolicy,
    )

    run_dir = tmp_path / "run"
    expected = [Objective("latency_ms", "min"), Objective("throughput", "max")]
    OpenEvolveSearchPolicy(
        state_dir=run_dir / "logs" / "openevolve",
        seed=1,
        config=None,
        objectives=expected,
    )

    with patch.object(cli, "_resume_exp_dir", return_value=run_dir):
        restored = cli._restore_openevolve_objectives(  # noqa: SLF001  # tracked: #288
            [],
            existing=True,
            exp_name="run",
            runs_dir=tmp_path,
        )

    assert restored == expected


def test_openevolve_override_selects_policy_on_new_run(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_evolve_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--openevolve-num-islands", "3"]
    )

    policy_name, resolved = cli._resolve_openevolve_options(  # noqa: SLF001  # tracked: #288
        args,
        existing=False,
        exp_name="new",
        runs_dir=tmp_path,
    )

    assert policy_name == "openevolve"
    assert resolved is not None
    assert resolved.num_islands == 3


def test_validate_evolve_rejects_openevolve_knob_with_vibesys_policy(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_evolve_parser().parse_args(  # noqa: SLF001  # tracked: #288
        [
            "--input",
            str(bundle),
            "--search-policy",
            "vibesys",
            "--openevolve-num-islands",
            "3",
        ]
    )

    with pytest.raises(ConfigurationError):
        cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288


@pytest.mark.parametrize(
    "flag,value",  # noqa: PT006  # tracked: #288
    [
        ("--openevolve-population-size", "0"),
        ("--openevolve-archive-size", "0"),
        ("--openevolve-num-islands", "0"),
        ("--openevolve-migration-interval", "0"),
        ("--openevolve-migration-rate", "-0.1"),
        ("--openevolve-migration-rate", "1.1"),
    ],
)
def test_validate_evolve_rejects_invalid_openevolve_config(tmp_path, flag, value):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = cli._build_evolve_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--search-policy", "openevolve", flag, value]
    )

    with pytest.raises(ConfigurationError):
        cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288


def test_max_parallelism_defaults_to_one_and_parses(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """--max-parallelism is serial (1) by default and accepts an int override."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288

    assert parser.parse_args(["--input", str(bundle)]).max_parallelism == 1
    assert (
        parser.parse_args(["--max-parallelism", "4", "--input", str(bundle)]).max_parallelism == 4
    )


def test_validate_evolve_rejects_nonpositive_parallelism(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """--max-parallelism < 1 is a configuration error."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288
    args = parser.parse_args(["--max-parallelism", "0", "--input", str(bundle)])
    with pytest.raises(ConfigurationError):
        cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288


def test_validate_evolve_defers_parallelism_support_to_environment(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """CLI validation does not hard-code one parallel-capable provider."""
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    parser = cli._build_evolve_parser()  # noqa: SLF001  # tracked: #288

    # The loop reads the selected environment capability and may downgrade to
    # serial; the CLI must not assume which providers support isolation.
    args = parser.parse_args(["--max-parallelism", "4", "--input", str(bundle)])
    cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288

    # Modal remains one supported adapter, not a special loop policy.
    args = parser.parse_args(
        ["--max-parallelism", "4", "--modal", "--profiler", "torch", "--input", str(bundle)]
    )
    cli._validate_evolve(args)  # noqa: SLF001  # tracked: #288


@pytest.mark.parametrize(
    "argv",
    [
        ["--profiler", "bogus"],
    ],
)
def test_agent_parser_rejects_invalid_enum_args(argv):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    with pytest.raises(ConfigurationError) as exc:
        _build_agent_parser().parse_args(argv)

    assert exc.value.diagnostic.code == "invalid_arguments"


def test_agent_parser_rejects_obsolete_target_flags():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    with pytest.raises(ConfigurationError):
        _build_agent_parser().parse_args(["--ref", "examples/Llama-3-8B/reference"])


def test_validate_target_inputs_loads_manifest(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = _build_agent_parser().parse_args(["--input", str(bundle)])

    _validate_target_inputs(args)

    assert args.input_bundle.root == bundle.resolve()
    assert args.input_bundle.domain is DomainName.GENERIC
    assert args.input_bundle.accuracy_command_display == "uv run python accuracy_checker/checker.py"
    assert args.input_bundle.benchmark_command_display == "uv run python benchmark/benchmark.py"


def test_agent_parser_rejects_domain_override_flag():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    with pytest.raises(ConfigurationError):
        _build_agent_parser().parse_args(["--domain", "llm-serving"])


def test_validate_target_inputs_loads_trusted_benchmark_result_contract(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    manifest = bundle / "vibesys.input.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[benchmark.result]\njson_argument = '--output-json'\nmetric = 'ops_per_sec'\n"
    )
    args = _build_agent_parser().parse_args(["--input", str(bundle)])

    _validate_target_inputs(args)

    assert args.input_bundle.benchmark_result is not None
    assert args.input_bundle.benchmark_result.json_argument == "--output-json"
    assert args.input_bundle.benchmark_result.metric == "ops_per_sec"


def test_validate_target_inputs_rejects_missing_input_dir(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    missing = tmp_path / "missing"
    args = _build_agent_parser().parse_args(["--input", str(missing)])

    with pytest.raises(ConfigurationError) as exc:
        _validate_target_inputs(args)

    assert "--input path does not exist" in exc.value.diagnostic.message


def test_validate_target_inputs_reports_missing_manifest(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = tmp_path / "incomplete"
    bundle.mkdir()
    (bundle / "OBJECTIVE.md").write_text("objective\n")

    args = _build_agent_parser().parse_args(["--input", str(bundle)])

    with pytest.raises(ConfigurationError) as exc:
        _validate_target_inputs(args)

    assert "vibesys.input.toml" in exc.value.diagnostic.message


def test_validate_target_inputs_reports_missing_command(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    tools = bundle / "tools"
    tools.mkdir()
    (tools / "check").write_text("#!/usr/bin/env bash\nexit 0\n")
    (tools / "check").chmod(0o755)
    (bundle / "vibesys.input.toml").write_text(
        """
version = 1

[agent]
domain = "generic"

[accuracy]
command = ["./tools/check"]

[benchmark]
command = ["./tools/bench"]
""".lstrip()
    )
    args = _build_agent_parser().parse_args(["--input", str(bundle)])

    with pytest.raises(ConfigurationError) as exc:
        _validate_target_inputs(args)

    assert "benchmark.command executable does not exist" in exc.value.diagnostic.message


def test_validate_target_inputs_requires_input():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    args = _build_agent_parser().parse_args([])

    with pytest.raises(ConfigurationError) as exc:
        _validate_target_inputs(args)

    message = exc.value.diagnostic.message
    assert "missing required target input" in message
    # The error points at both ways to supply a target.
    assert "--input" in message
    assert "--input-objective" in message


def test_trusted_input_baseline_requires_resume(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser, _validate_agent  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = _build_agent_parser().parse_args(
        ["--input", str(bundle), "--trusted-input-baseline", "HEAD"]
    )

    with pytest.raises(ConfigurationError) as exc:
        _validate_agent(args)

    assert "--trusted-input-baseline requires --resume" in exc.value.diagnostic.message


def test_validate_agent_rejects_nonpositive_judge_cadence(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser, _validate_agent  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = _build_agent_parser().parse_args(["--input", str(bundle), "--judge-every", "0"])

    with pytest.raises(ConfigurationError) as exc:
        _validate_agent(args)

    assert "--judge-every must be >= 1" in exc.value.diagnostic.message


def test_validate_agent_rejects_nonpositive_official_evaluation_cadence(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser, _validate_agent  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = _build_agent_parser().parse_args(["--input", str(bundle), "--official-eval-every", "0"])

    with pytest.raises(ConfigurationError) as exc:
        _validate_agent(args)

    assert "--official-eval-every must be >= 1" in exc.value.diagnostic.message


def test_agent_operator_constraints_are_repeatable_and_do_not_mutate_objective():  # noqa: ANN201  # tracked: #288
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    args = _build_agent_parser().parse_args(
        ["--constraint", "No quantization.", "--constraint", "  One H100 only.  "]
    )
    objective = "Maximize throughput.\n"

    effective = _with_operator_constraints(objective, args.constraint)

    assert objective == "Maximize throughput.\n"
    assert effective.endswith("## Operator constraints\n\n- No quantization.\n- One H100 only.\n")


def test_stub_agent_smoke_defaults_supply_input_and_unique_exp_name():  # noqa: ANN201  # tracked: #288
    argv = _prepare_stub_agent_smoke_defaults(["--stub-agent", "--max-rounds", "1"])

    assert argv[:2] == ["--input", str(Path("examples/data-structures/queue-spsc").resolve())]
    assert argv[2] == "--exp-name"
    assert argv[3].startswith("stub-smoke-")
    assert argv[-2:] == ["--max-rounds", "1"]


def test_stub_agent_smoke_defaults_preserve_explicit_input():  # noqa: ANN201  # tracked: #288
    argv = ["--stub-agent", "--input", "examples/kv-store"]

    assert _prepare_stub_agent_smoke_defaults(argv) == argv


def test_stub_agent_can_run_without_agent_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    args = cli._build_agent_parser().parse_args(  # noqa: SLF001  # tracked: #288
        [
            "--stub-agent",
            "--input",
            str(bundle),
            "--no-skills",
        ]
    )
    _validate_target_inputs(args)

    config, skills, _ = load_config_and_skills(args, domain=DomainName.GENERIC)

    assert config.model.name == "gpt-5.5"
    assert skills is None


def test_config_help_describes_launch_directory_discovery() -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    help_text = " ".join(
        cli._build_agent_parser().format_help().split()  # noqa: SLF001  # tracked: #288
    )

    assert "agent.toml in the launch working directory" in help_text
    assert "a missing file is an error" in help_text


def test_omitted_config_reports_missing_launch_directory_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = cli._build_agent_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--local", "--no-skills"]
    )

    with pytest.raises(ConfigurationError) as exc:
        load_config_and_skills(args, domain=DomainName.GENERIC)

    assert exc.value.diagnostic.code == "config_load_failed"
    assert exc.value.diagnostic.stage == "config_loading"
    assert str(tmp_path / "agent.toml") in exc.value.diagnostic.message


def test_omitted_config_does_not_search_parent_for_agent_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    (tmp_path / "agent.toml").write_text('[model]\nname = "parent-model"\n')
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    args = cli._build_agent_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--local", "--no-skills"]
    )

    with pytest.raises(ConfigurationError) as exc:
        load_config_and_skills(args, domain=DomainName.GENERIC)

    assert exc.value.diagnostic.code == "config_load_failed"
    assert str(launch_dir / "agent.toml") in exc.value.diagnostic.message
    assert "parent-model" not in exc.value.diagnostic.message


def test_omitted_config_loads_launch_directory_config_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    (launch_dir / "agent.toml").write_text('[model]\nname = "launch-model"\n')
    monkeypatch.chdir(launch_dir)
    args = cli._build_agent_parser().parse_args(  # noqa: SLF001  # tracked: #288
        ["--input", str(bundle), "--local", "--no-skills"]
    )

    config, _, _ = load_config_and_skills(args, domain=DomainName.GENERIC)

    assert args.config is None
    assert config.model.name == "launch-model"


@pytest.mark.parametrize("stub_args", [[], ["--stub-agent"]])
def test_missing_explicit_config_reports_configuration_error(
    tmp_path: Path,
    stub_args: list[str],
) -> None:
    from vibesys.main import _build_agent_parser  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    args = _build_agent_parser().parse_args(
        [
            *stub_args,
            "--input",
            str(bundle),
            "--config",
            str(tmp_path / "missing-agent.toml"),
        ]
    )

    with pytest.raises(ConfigurationError) as exc:
        load_config_and_skills(args, domain=DomainName.GENERIC)

    assert exc.value.diagnostic.code == "config_load_failed"
    assert exc.value.diagnostic.stage == "config_loading"


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


def test_tui_defaults_uses_launch_directory_config_and_generated_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json  # noqa: PLC0415  # tracked: #288

    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """\
[model]
name = "gpt-5.5"

[repository]
owner = "vibesys-playground"
visibility = "private"
"""
    )
    input_path = tmp_path / "Queue MPSC"
    input_path.mkdir()
    monkeypatch.chdir(tmp_path)
    argv = [
        "vibesys",
        "tui-defaults",
        "--input",
        str(input_path),
    ]

    with patch.object(sys, "argv", argv):
        main()

    defaults = json.loads(capsys.readouterr().out)
    assert defaults["repository_owner"] == "vibesys-playground"
    assert defaults["visibility"] == "private"
    assert defaults["experiment_name"].startswith("queue-mpsc-")
    assert defaults["repository_name"] == defaults["experiment_name"]
    assert defaults["theme"] == "dark"
    assert defaults["runs_dir"] == str((tmp_path / "exp_env").resolve())


def test_tui_defaults_supports_first_launch_without_github_authentication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json  # noqa: PLC0415  # tracked: #288

    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    github = Mock()
    github.current_user.side_effect = cli.GitHubCLIError("GitHub CLI is unavailable")
    monkeypatch.setattr(cli, "GitHubCLI", Mock(return_value=github))
    (tmp_path / "agent.toml").write_text('[model]\nname = "gpt-5.4"\n')
    monkeypatch.chdir(tmp_path)

    with patch.object(sys, "argv", ["vibesys", "tui-defaults"]):
        main()

    defaults = json.loads(capsys.readouterr().out)
    assert defaults["runs_dir"] == str((tmp_path / "exp_env").resolve())
    assert defaults["input_path"] == ""
    assert defaults["experiment_name"].startswith("experiment-")
    assert defaults["repository_owner"] is None
    assert defaults["repository_name"] == defaults["experiment_name"]
    assert defaults["visibility"] == "private"
    assert defaults["theme"] == "dark"
    github.current_user.assert_called_once_with()


def test_tui_defaults_reports_missing_launch_directory_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with (
        patch.object(sys, "argv", ["vibesys", "tui-defaults"]),
        pytest.raises(SystemExit) as exc,
    ):
        main()

    assert exc.value.code == 2
    assert str(tmp_path / "agent.toml") in capsys.readouterr().err


def test_tui_defaults_rejects_an_explicit_missing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing-agent.toml"

    with (
        patch.object(sys, "argv", ["vibesys", "tui-defaults", "--config", str(missing)]),
        pytest.raises(SystemExit) as exc,
    ):
        main()

    assert exc.value.code == 2
    assert str(missing) in capsys.readouterr().err


def test_tui_defaults_resolves_an_explicit_runs_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json  # noqa: PLC0415  # tracked: #288

    monkeypatch.chdir(tmp_path)
    argv = [
        "vibesys",
        "tui-defaults",
        "--config",
        str(_write_theme_config(tmp_path, None)),
        "--runs-dir",
        "selected-runs",
    ]

    with patch.object(sys, "argv", argv):
        main()

    assert json.loads(capsys.readouterr().out)["runs_dir"] == str(
        (tmp_path / "selected-runs").resolve()
    )


@pytest.mark.parametrize("value", ["", " \t "])
def test_tui_defaults_parser_rejects_an_empty_runs_directory(value: str) -> None:
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    with pytest.raises(ConfigurationError) as exc:
        cli._build_tui_defaults_parser().parse_args([f"--runs-dir={value}"])  # noqa: SLF001  # tracked: #288

    assert exc.value.diagnostic.code == "invalid_arguments"
    assert exc.value.diagnostic.stage == "argument_parsing"
    assert "--runs-dir" in exc.value.diagnostic.message


def test_tui_defaults_supports_configless_stub_directory_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json  # noqa: PLC0415  # tracked: #288

    monkeypatch.chdir(tmp_path)
    argv = [
        "vibesys",
        "tui-defaults",
        "--stub-agent",
        "--directory-only",
    ]

    with patch.object(sys, "argv", argv):
        main()

    defaults = json.loads(capsys.readouterr().out)
    assert defaults["runs_dir"] == str((tmp_path / "exp_env").resolve())
    assert defaults["repository_owner"] is None


def _write_theme_config(tmp_path, theme: str | None) -> Path:  # noqa: ANN001  # tracked: #288
    config_path = tmp_path / "agent.toml"
    tui_section = f'\n[tui]\ntheme = "{theme}"\n' if theme is not None else ""
    # An explicit owner keeps the theme assertions off the `gh auth` fallback
    # that an omitted owner now resolves through.
    config_path.write_text(
        f'[model]\nname = "gpt-5.5"\n\n[repository]\nowner = "vibesys-playground"\n{tui_section}'
    )
    return config_path


def test_tui_defaults_reports_the_configured_theme(tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    import json  # noqa: PLC0415  # tracked: #288

    argv = [
        "vibesys",
        "tui-defaults",
        "--config",
        str(_write_theme_config(tmp_path, "catppuccin-latte")),
    ]

    with patch.object(sys, "argv", argv):
        main()

    assert json.loads(capsys.readouterr().out)["theme"] == "catppuccin-latte"


def test_tui_defaults_theme_flag_overrides_the_configured_theme(tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    import json  # noqa: PLC0415  # tracked: #288

    argv = [
        "vibesys",
        "tui-defaults",
        "--config",
        str(_write_theme_config(tmp_path, "catppuccin-latte")),
        "--theme",
        "high-contrast-dark",
    ]

    with patch.object(sys, "argv", argv):
        main()

    assert json.loads(capsys.readouterr().out)["theme"] == "high-contrast-dark"


def test_tui_defaults_rejects_an_unknown_theme(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    argv = [
        "vibesys",
        "tui-defaults",
        "--config",
        str(_write_theme_config(tmp_path, None)),
        "--theme",
        "monokai",
    ]

    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2


def test_validate_command_defaults_to_current_input_bundle(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)
    monkeypatch.chdir(bundle)
    monkeypatch.setattr(sys, "argv", ["vibesys", "validate"])

    main()

    output = capsys.readouterr().out
    assert "VibeSys validation passed" in output
    assert f"input bundle: {bundle}" in output
    assert "accuracy command: uv run python accuracy_checker/checker.py" in output
    assert "benchmark command: uv run python benchmark/benchmark.py" in output


def test_validate_command_accepts_input_bundle_path(tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)
    argv = ["vibesys", "validate", str(bundle)]

    with patch.object(sys, "argv", argv):
        main()

    output = capsys.readouterr().out
    assert "input bundle is valid" in output
    assert f"objective: {bundle / 'OBJECTIVE.md'}" in output


def test_validate_command_rejects_run_input_flag(tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)
    argv = ["vibesys", "validate", "--input", str(bundle)]

    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_all_example_input_bundles_pass_validate(example_input_bundles: tuple[Path, ...], capsys):  # noqa: ANN001, ANN201  # tracked: #288
    for input_bundle in example_input_bundles:
        argv = ["vibesys", "validate", str(input_bundle)]
        with patch.object(sys, "argv", argv):
            main()

    output = capsys.readouterr().out
    assert output.count("VibeSys validation passed") == len(example_input_bundles)


def test_validate_command_reports_invalid_harness_without_running_agent(tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)
    (bundle / "OBJECTIVE.md").unlink()
    argv = ["vibesys", "validate", str(bundle)]

    runner = Mock()
    with (
        patch.object(sys, "argv", argv),
        _patch_loop_runner("agent", runner),
        pytest.raises(SystemExit) as exc,
    ):
        main()

    assert exc.value.code == 1
    runner.assert_not_called()
    error = capsys.readouterr().err
    assert "Validation failed for input bundle" in error
    assert "OBJECTIVE.md not found" in error


def test_resume_without_input_infers_original_input(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    run_dir = tmp_path / "exp_env" / "20260716-180256-test"
    _write_resume_event(run_dir, bundle)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    invocation = parse_cli_invocation(
        [
            "--outer-loop",
            "agent",
            "--runs-dir",
            str(tmp_path / "exp_env"),
            "--resume",
            "20260716-180256-test",
        ]
    )

    assert invocation.args.resume == "20260716-180256-test"
    assert invocation.args.input == bundle
    assert invocation.args.input_bundle.root == bundle.resolve()


def test_resume_accepts_exp_env_path_without_input(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    bundle = _write_input_bundle(tmp_path)
    run_dir = tmp_path / "exp_env" / "20260716-180256-test"
    _write_resume_event(run_dir, bundle)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    invocation = parse_cli_invocation(
        [
            "--outer-loop",
            "agent",
            "--runs-dir",
            str(tmp_path / "exp_env"),
            "--resume",
            str(run_dir),
        ]
    )

    assert invocation.args.resume == "20260716-180256-test"
    assert invocation.args.input_bundle.root == bundle.resolve()


def test_resume_accepts_external_local_clone_and_materialized_input(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    run_dir = tmp_path / "clone"
    workspace = _write_input_bundle(run_dir)
    # These source locations are intentionally unavailable in a clone. They
    # were already copied into the workspace by the original fresh run.
    manifest = workspace / "vibesys.input.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[workspace]\nseed = "../../starters/missing"\n'
        + '\n[evaluator]\nsource = "../../evaluators/missing"\n'
    )
    experiment = run_dir / "experiment"
    experiment.mkdir()
    workspace.rename(experiment / "workspace")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path / "project")

    invocation = parse_cli_invocation(
        [
            "--outer-loop",
            "agent",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--resume",
            str(experiment),
        ]
    )

    assert invocation.args.resume == str(experiment.resolve())
    assert invocation.args.input_bundle.root == (experiment / "workspace").resolve()
    assert invocation.args.input_bundle.workspace_seed_path is None
    assert invocation.args.input_bundle.evaluator_path is None


def test_resume_github_repo_clones_into_exp_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):  # noqa: ANN001, ANN003, ANN202  # tracked: #288
        commands.append(command)
        if command[:3] == ["gh", "repo", "clone"]:
            Path(command[-1]).mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli, "GitHubCLI", lambda: GitHubCLI(_runner=fake_run))

    assert _resolve_run_dir("vibesys-playground/trial", tmp_path / "exp_env") == "trial"
    assert commands == [
        ["gh", "auth", "status", "--hostname", "github.com"],
        ["gh", "repo", "clone", "vibesys-playground/trial", str(tmp_path / "exp_env/trial")],
    ]


def test_resume_github_repo_reuses_matching_local_clone(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    destination = tmp_path / "exp_env" / "trial"
    destination.mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    def fake_run(command, **_kwargs):  # noqa: ANN001, ANN003, ANN202  # tracked: #288
        assert command == ["git", "remote", "get-url", "origin"]
        return subprocess.CompletedProcess(
            command,
            0,
            "git@github.com:vibesys-playground/trial.git\n",
            "",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        cli,
        "GitHubCLI",
        lambda: pytest.fail("matching local clone should not invoke GitHub CLI"),
    )

    assert _resolve_run_dir("vibesys-playground/trial", tmp_path / "exp_env") == "trial"


def test_resume_github_repo_explains_missing_authentication(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    def fake_run(command, **_kwargs):  # noqa: ANN001, ANN003, ANN202  # tracked: #288
        return subprocess.CompletedProcess(command, 1, "", "not logged into any GitHub hosts")

    monkeypatch.setattr(cli, "GitHubCLI", lambda: GitHubCLI(_runner=fake_run))

    with pytest.raises(ConfigurationError) as exc:
        _resolve_run_dir("vibesys-playground/trial", tmp_path / "exp_env")

    assert exc.value.diagnostic.code == "resume_clone_failed"
    assert "gh auth login --hostname github.com" in exc.value.diagnostic.message
    assert "not logged into any GitHub hosts" in exc.value.diagnostic.message


def test_resume_rejects_creating_a_second_repository(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)

    with pytest.raises(ConfigurationError) as exc:
        parse_cli_invocation(
            [
                "--outer-loop",
                "agent",
                "--input",
                str(bundle),
                "--runs-dir",
                str(tmp_path / "exp_env"),
                "--resume",
                "run",
                "--repo",
                "my-lab/run",
            ]
        )

    assert exc.value.diagnostic.code == "invalid_arguments"


def test_resume_latest_without_input_uses_latest_run_metadata(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    older_bundle = _write_input_bundle(tmp_path / "older")
    latest_bundle = _write_input_bundle(tmp_path / "latest")
    older = tmp_path / "exp_env" / "20260716-100000-test"
    latest = tmp_path / "exp_env" / "20260716-180256-test"
    _write_resume_event(older, older_bundle)
    _write_resume_event(latest, latest_bundle)
    (tmp_path / "exp_env" / "_inputs").mkdir()
    (tmp_path / "exp_env" / ".cache").mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    invocation = parse_cli_invocation(
        ["--outer-loop", "agent", "--runs-dir", str(tmp_path / "exp_env"), "--resume"]
    )

    assert invocation.args.resume == "20260716-180256-test"
    assert invocation.args.input_bundle.root == latest_bundle.resolve()


def test_resume_latest_reports_missing_exp_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ConfigurationError) as exc:
        _resolve_run_dir("latest", tmp_path / "exp_env")

    assert exc.value.diagnostic.code == "resume_not_found"


def test_resume_latest_reports_empty_exp_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    (tmp_path / "exp_env").mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ConfigurationError) as exc:
        _resolve_run_dir("latest", tmp_path / "exp_env")

    assert exc.value.diagnostic.code == "resume_not_found"


def test_resume_without_input_reports_missing_metadata(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    import vibesys.main as cli  # noqa: PLC0415  # tracked: #288

    (tmp_path / "exp_env" / "20260716-180256-test" / "logs").mkdir(parents=True)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ConfigurationError) as exc:
        parse_cli_invocation(
            [
                "--outer-loop",
                "agent",
                "--runs-dir",
                str(tmp_path / "exp_env"),
                "--resume",
                "20260716-180256-test",
            ]
        )

    assert exc.value.diagnostic.code == "resume_input_not_found"
    assert exc.value.diagnostic.stage == "resume_resolution"


def test_resume_input_ignores_blank_and_non_run_events(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _write_input_bundle(tmp_path)
    run_dir = tmp_path / "exp_env" / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "run-events.jsonl").write_text(
        "\n"
        '{"type": "server_started", "data": null}\n'
        f'{{"type": "run_started", "data": {{"input": "{bundle}"}}}}\n'
    )

    assert _infer_resume_input(run_dir) == bundle


def test_resume_input_reports_invalid_json(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    run_dir = tmp_path / "exp_env" / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "run-events.jsonl").write_text("{not-json}\n")

    with pytest.raises(ConfigurationError) as exc:
        _infer_resume_input(run_dir)

    assert exc.value.diagnostic.code == "resume_input_invalid"


@pytest.mark.parametrize(
    "event",
    [
        {"type": "run_started", "data": None},
        {"type": "run_started", "data": {}},
    ],
)
def test_resume_input_reports_missing_input_in_run_started(tmp_path, event):  # noqa: ANN001, ANN201  # tracked: #288
    import json  # noqa: PLC0415  # tracked: #288

    run_dir = tmp_path / "exp_env" / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "run-events.jsonl").write_text(json.dumps(event) + "\n")

    with pytest.raises(ConfigurationError) as exc:
        _infer_resume_input(run_dir)

    assert exc.value.diagnostic.code == "resume_input_not_found"


def test_resume_round_defaults_when_rounds_json_missing_or_invalid(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    exp_dir = tmp_path / "run"

    assert _detect_resume_round(exp_dir) == 1

    logs = exp_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "rounds.json").write_text("not-json")

    assert _detect_resume_round(exp_dir) == 1


def test_resume_round_counts_round_entries_and_prunes_later_rounds(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    rounds_json = tmp_path / "run" / "logs" / "rounds.json"
    rounds_json.parent.mkdir(parents=True)
    rounds_json.write_text('[{"round": 1}, {"round": 2}, {"round": 3}]')
    active_hypothesis = rounds_json.parent / "active_hypothesis.json"
    active_hypothesis.write_text('{"started_round": 3}')

    assert _detect_resume_round(tmp_path / "run") == 4

    _prune_rounds_state(tmp_path / "run", keep_up_to=3)

    assert rounds_json.read_text() == '[\n  {\n    "round": 1\n  },\n  {\n    "round": 2\n  }\n]'
    assert not active_hypothesis.exists()


@pytest.mark.parametrize(
    "spec,message",  # noqa: PT006  # tracked: #288
    [
        ("latency", "must be 'name:max' or 'name:min'"),
        (":max", "metric name is empty"),
        ("latency:avg", "direction must be 'max' or 'min'"),
    ],
)
def test_parse_cli_objective_rejects_malformed_specs(spec, message):  # noqa: ANN001, ANN201  # tracked: #288
    with pytest.raises(Exception) as exc:  # noqa: PT011  # tracked: #288
        _parse_cli_objective(spec)

    assert message in str(exc.value)


def test_load_objectives_toml_reports_malformed_entries(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    (tmp_path / "objectives.toml").write_text(
        """
[[objective]]
name = "latency"
direction = "avg"
""".lstrip()
    )

    with pytest.raises(ValueError, match="Malformed entry"):
        _load_objectives_toml(tmp_path)


def test_load_pareto_relative_noise_toml_is_opt_in(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    assert _load_pareto_relative_noise_toml(tmp_path) == 0.0

    (tmp_path / "objectives.toml").write_text("[pareto]\nrelative_noise = 0.03\n")

    assert _load_pareto_relative_noise_toml(tmp_path) == 0.03


@pytest.mark.parametrize("value", ["true", "-0.1", "1.0", '"noisy"'])
def test_load_pareto_relative_noise_toml_rejects_invalid_values(tmp_path, value):  # noqa: ANN001, ANN201  # tracked: #288
    (tmp_path / "objectives.toml").write_text(f"[pareto]\nrelative_noise = {value}\n")

    with pytest.raises(ValueError, match="pareto.relative_noise"):  # noqa: RUF043  # tracked: #288
        _load_pareto_relative_noise_toml(tmp_path)


def test_control_socket_from_argv_handles_empty_equals_and_space_form():  # noqa: ANN201  # tracked: #288
    assert _control_socket_from_argv(["--control-socket="]) is None
    assert _control_socket_from_argv(["--control-socket", "/tmp/vs.sock"]) == Path("/tmp/vs.sock")  # noqa: S108  # tracked: #288


def test_render_configuration_error_prints_usage(capsys):  # noqa: ANN001, ANN201  # tracked: #288
    diagnostic = ConfigurationError(
        diagnostic=ConfigurationDiagnostic(
            code="invalid_arguments",
            stage="argument_parsing",
            message="bad args",
            usage="usage: vibesys ...",
        )
    )

    with pytest.raises(SystemExit) as exc:
        _render_configuration_error(diagnostic)

    assert exc.value.code == 2
    assert "bad args" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() routes to the right runner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loop_name", ["agent", "evolve", "plain"])
def test_main_routes_to_runner(loop_name: str):  # noqa: ANN201  # tracked: #288
    argv = ["vibesys", "--outer-loop", loop_name, "--exp-name", "x", *TARGET_ARGS]
    runner = Mock()
    with patch.object(sys, "argv", argv), _patch_loop_runner(loop_name, runner):
        main()
        runner.assert_called_once()
        args = runner.call_args.args[0]
        assert args.exp_name == "x"
        assert args.input_bundle.root.name == "Llama-3-8B"


def test_main_tty_run_stays_in_python_cli():  # noqa: ANN201  # tracked: #288
    argv = [
        "vibesys",
        "--outer-loop",
        "agent",
        "--exp-name",
        "x",
        *TARGET_ARGS,
    ]
    runner = Mock()
    with (
        patch.object(sys, "argv", argv),
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(sys.stdout, "isatty", return_value=True),
        _patch_loop_runner("agent", runner),
    ):
        main()

    runner.assert_called_once()


def test_main_headless_skips_tui():  # noqa: ANN201  # tracked: #288
    argv = [
        "vibesys",
        "--outer-loop",
        "agent",
        "--headless",
        *TARGET_ARGS,
    ]
    runner = Mock()
    with (
        patch.object(sys, "argv", argv),
        _patch_loop_runner("agent", runner),
    ):
        main()
    runner.assert_called_once()
