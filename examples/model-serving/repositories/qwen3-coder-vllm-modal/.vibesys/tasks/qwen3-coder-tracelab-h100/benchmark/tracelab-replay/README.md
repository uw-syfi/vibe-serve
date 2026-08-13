# TraceLab replay benchmark support

This directory is trusted, task-owned benchmark infrastructure. VibeSys keeps
the enclosing `.vibesys` tree read-only to coding agents.

The benchmark uses TraceLab's own Rust `session_runner` and the pinned public
TraceLab `v0.0.1` DuckDB release. `run_tracelab_replay.py` downloads and
verifies the public trace data, then invokes the pinned TraceLab submodule.
