import inspect

import agentshim

import vibesys._agent_cli.cli_agent


def test_recorder_api_removed_in_favor_of_agent_event_handler():  # noqa: ANN202  # tracked: #288
    assert not hasattr(agentshim, "trajectory")
    assert (
        "recorder"
        not in inspect.signature(vibesys._agent_cli.cli_agent.CLICodingAgent.__init__).parameters  # noqa: SLF001  # tracked: #288
    )
    assert (
        "recorder"
        not in inspect.signature(
            vibesys._agent_cli.cli_agent.CLIGenerationSession.__init__  # noqa: SLF001  # tracked: #288
        ).parameters
    )
