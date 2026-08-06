"""Autonomous orchestrator-driven build loop.

The :func:`run_agent_loop` entry point lets an *orchestrator* agent decide per
round what task the implementer should tackle, judged by pass criteria the
orchestrator writes and optionally informed by a profiling pass it requests.
"""
