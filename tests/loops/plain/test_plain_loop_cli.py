"""Tests for the issue outer-loop CLI parser and main()."""

from unittest.mock import patch

import pytest

from vibesys.loops.plain.loop import PlainLoopState
from vibesys.main import _build_plain_parser as build_parser
from vibesys.main import main

TARGET_ARGS = [
    "--input",
    "examples/model-serving/Llama-3-8B",
]


class TestBuildParser:
    def test_default_max_rounds(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args([])
        assert args.max_rounds == 5

    def test_default_max_attempts_per_issue(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args([])
        assert args.max_attempts_per_issue == 3

    def test_default_max_issues_per_perf_eval(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args([])
        assert args.max_issues_per_perf_eval == 3

    def test_default_resume_is_none(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args([])
        assert args.resume is None

    def test_resume_without_value_defaults_to_latest(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args(["--resume"])
        assert args.resume == "latest"

    def test_resume_with_explicit_dir(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args(["--resume", "20260408-090000-test"])
        assert args.resume == "20260408-090000-test"

    def test_overrides_for_rounds(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args(
            [
                "--max-rounds",
                "10",
                "--max-attempts-per-issue",
                "5",
                "--max-issues-per-perf-eval",
                "2",
            ]
        )
        assert args.max_rounds == 10
        assert args.max_attempts_per_issue == 5
        assert args.max_issues_per_perf_eval == 2

    def test_common_args_present(self):  # noqa: ANN201  # tracked: #288
        parser = build_parser()
        args = parser.parse_args(["--exp-name", "myexp"])
        assert args.exp_name == "myexp"
        assert args.input is None
        assert hasattr(args, "docker")
        assert hasattr(args, "debug")


class TestMain:
    _BASE_ARGV = ["vibesys", "--outer-loop", "plain", "--local", *TARGET_ARGS]  # noqa: RUF012  # tracked: #288

    def _patch_run(self, return_value: bool):  # noqa: ANN202, FBT001  # tracked: #288
        return patch(
            "vibesys.loops.plain.loop.run_plain_loop",
            return_value=return_value,
        )

    def _patch_config(self):  # noqa: ANN202  # tracked: #288
        from vibesys.constants import DEFAULT_COMPUTE_BACKEND  # noqa: PLC0415  # tracked: #288

        return patch(
            "vibesys.main.load_config_and_skills",
            return_value=(
                {"model": {"name": "claude-sonnet-4-6"}},
                None,
                DEFAULT_COMPUTE_BACKEND,
            ),
        )

    def test_main_exits_zero_on_success(self):  # noqa: ANN201  # tracked: #288
        with patch("sys.argv", list(self._BASE_ARGV)):  # noqa: SIM117  # tracked: #288
            with self._patch_config(), self._patch_run(True):  # noqa: FBT003  # tracked: #288
                main()

    def test_main_exits_one_on_failure(self):  # noqa: ANN201  # tracked: #288
        with patch("sys.argv", list(self._BASE_ARGV)):  # noqa: SIM117  # tracked: #288
            with self._patch_config(), self._patch_run(False):  # noqa: FBT003  # tracked: #288
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1

    def test_main_passes_round_args_to_run_loop(self):  # noqa: ANN201  # tracked: #288
        with (
            patch(
                "sys.argv",
                [
                    *self._BASE_ARGV,
                    "--max-rounds",
                    "7",
                    "--max-attempts-per-issue",
                    "4",
                    "--max-issues-per-perf-eval",
                    "2",
                ],
            ),
            self._patch_config(),
            patch(
                "vibesys.loops.plain.loop.run_plain_loop",
                return_value=True,
            ) as mock_run,
        ):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["max_rounds"] == 7
            assert kwargs["max_attempts_per_issue"] == 4
            assert kwargs["max_issues_per_perf_eval"] == 2

    def test_main_start_round_overrides_loaded_state(self, tmp_path):  # noqa: ANN001, ANN201, ARG002  # tracked: #288
        with (
            patch(
                "sys.argv",
                [
                    *self._BASE_ARGV,
                    "--resume",
                    "fake-run-dir",
                    "--start-round",
                    "3",
                ],
            ),
            self._patch_config(),
            patch(
                "vibesys.main._resolve_run_dir",
                return_value="fake-run-dir",
            ),
            patch(
                "vibesys.loops.plain.loop.run_plain_loop",
                return_value=True,
            ) as mock_run,
        ):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["existing"] is True
            state = kwargs["resume_state"]
            assert isinstance(state, PlainLoopState)
            assert state.round_idx == 2  # 0-indexed
            assert state.bootstrap_done is True

    def test_main_forwards_agent_backend_and_cli_provider(self):  # noqa: ANN201  # tracked: #288
        with (
            patch(
                "sys.argv",
                [
                    *self._BASE_ARGV,
                    "--agent-backend",
                    "cli",
                    "--cli-provider",
                    "claude",
                ],
            ),
            self._patch_config(),
            patch(
                "vibesys.loops.plain.loop.run_plain_loop",
                return_value=True,
            ) as mock_run,
        ):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["agent_backend"] == "cli"
            assert kwargs["cli_provider"] == "claude"

    def test_main_defaults_agent_backend_and_cli_provider_to_none(self):  # noqa: ANN201  # tracked: #288
        with (
            patch("sys.argv", list(self._BASE_ARGV)),
            self._patch_config(),
            patch(
                "vibesys.loops.plain.loop.run_plain_loop",
                return_value=True,
            ) as mock_run,
        ):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["agent_backend"] is None
            assert kwargs["cli_provider"] is None

    @pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
    def test_main_accepts_all_cli_providers(self, provider):  # noqa: ANN001, ANN201  # tracked: #288
        """All four CLI providers must reach run_plain_loop without raising."""
        with (
            patch(
                "sys.argv",
                [
                    *self._BASE_ARGV,
                    "--agent-backend",
                    "cli",
                    "--cli-provider",
                    provider,
                ],
            ),
            self._patch_config(),
            patch(
                "vibesys.loops.plain.loop.run_plain_loop",
                return_value=True,
            ) as mock_run,
        ):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["agent_backend"] == "cli"
            assert kwargs["cli_provider"] == provider
