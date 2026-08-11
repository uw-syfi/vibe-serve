# TraceLab Replay Evaluator

Declare this directory as an ordinary `[evaluator]` source. VibeSys materializes
it at `_evaluator/tracelab-replay` in copied workspaces, where both the candidate
agent and evaluator commands can inspect it.

The benchmark uses TraceLab's own Rust `session_runner` and the pinned public
TraceLab `v0.0.1` DuckDB release. The input bundle contains a thin benchmark
shim, while the materialized evaluator directory contains the replay runner and
the code that downloads the public trace data.
