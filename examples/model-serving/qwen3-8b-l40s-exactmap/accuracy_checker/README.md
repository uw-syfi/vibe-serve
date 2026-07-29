# ExactMap accuracy and conformance checker

Run against a live candidate:

```bash
python checker.py --url http://localhost:8000
```

The checker uses the public API only. It validates ExactMap runtime identity,
strict streaming usage, prompt-conditioned behavior, deterministic greedy
decoding, and the output-token floor. It intentionally uses short requests so
correctness checks remain separate from the long-generation performance run.
