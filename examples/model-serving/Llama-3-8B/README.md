Llama-3-8B input bundle.

Use:

```bash
uv run vibesys --runs-dir "$PWD/exp_env" --local \
  --input examples/model-serving/Llama-3-8B
```

Each folder contains scripts plus a short README.
- `README.md` — this file.

Expected files for the VibeSys agent loop:
- reference_inference.py
- accuracy_check.py
- benchmark.py
- config.json
- requirements.txt (GPU dependencies for verifier/benchmark)

The CLI reads these files from this input bundle.

A separate `.venv/` is auto-created here by the verifier using `uv` with the dependencies from `requirements.txt` (torch, transformers, httpx).
