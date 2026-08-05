"""Live integration probe for the opt-in Omnigent backend.

Drives a real turn through OmnigentAgentRunner against the locally
authenticated CLI, in a throwaway workspace. Not a unit test -- it needs
credentials, network, and bwrap, which is why it lives outside the suite.

Usage: live_turn.py <provider> [model]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel

from vibesys.agents.omnigent.runner import OmnigentAgentRunner

provider = sys.argv[1] if len(sys.argv) > 1 else "claude"
model = sys.argv[2] if len(sys.argv) > 2 else None


class Answer(BaseModel):
    answer: str
    confidence: str


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=f"omni-live-{provider}-") as tmp:
        workspace = Path(tmp)
        (workspace / "NOTES.md").write_text("The launch code is 4417.\n")

        runner = OmnigentAgentRunner(
            provider=provider,
            model=model,
            model_name=model or provider,
            run_log_file=sys.stdout,
            log_dir=workspace,
        )
        print(f"== backend={runner.backend_name} provider={provider} model={model}")
        print(f"== workspace={workspace}")
        os_env = runner._build_os_env(workspace)
        print(f"== sandbox={os_env.sandbox.type} write_paths={os_env.sandbox.write_paths}")

        # 1) Plain text turn that requires READING a workspace file, which
        #    proves the executor is really rooted in the workspace.
        text = runner.invoke_text(
            kind="implementer",
            workspace=workspace,
            system_prompt="You are terse. Answer in one short sentence.",
            user_prompt="Read NOTES.md in the current directory and tell me the launch code.",
            round_label="live #1",
        )
        print(f"\n>>> TEXT RESULT: {text!r}")
        ok_read = "4417" in (text or "")

        # 2) Structured turn, exercising the schema hint + response parsing.
        parsed = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="You answer with JSON only.",
            user_prompt="What is 6 times 7? confidence should be 'high' or 'low'.",
            response_cls=Answer,
            fallback_factory=lambda: Answer(answer="FALLBACK", confidence="none"),
            round_label="live #2",
        )
        print(f"\n>>> STRUCTURED RESULT: {parsed!r}")
        ok_struct = parsed.answer != "FALLBACK" and "42" in parsed.answer

        # 3) Usage audit record must exist for both calls.
        usage_path = workspace / "usage.jsonl"
        lines = usage_path.read_text().splitlines() if usage_path.exists() else []
        print(f"\n>>> USAGE RECORDS: {len(lines)}")
        for line in lines:
            print("   ", line)
        ok_usage = len(lines) == 2

        print("\n==== RESULT ====")
        print(f"  workspace read through sandbox : {'PASS' if ok_read else 'FAIL'}")
        print(f"  structured response parsed     : {'PASS' if ok_struct else 'FAIL'}")
        print(f"  usage records written          : {'PASS' if ok_usage else 'FAIL'}")
        return 0 if (ok_read and ok_struct and ok_usage) else 1


if __name__ == "__main__":
    raise SystemExit(main())
