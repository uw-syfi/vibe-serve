# SGLang B200 Candidate Starter

Input bundles use `workspace.sources` to clone a pinned SGLang source tree into
`./sglang`; this starter provides a local bridge that forwards default evaluator
traffic to the candidate's Modal Blackwell (B200) app.

Typical local launch:

```bash
python serve.py
```

The bridge starts `modal serve main.py`, discovers the generated `*.modal.run`
URL, and listens on `http://localhost:8000` for the evaluator-owned benchmark
and accuracy checker. Set `MODAL_BACKEND_URL` to proxy to an existing Modal web
endpoint instead.

The candidate authors `main.py` (the Modal GPU app that launches
`sglang.launch_server` across the target B200 count) and may edit `sglang/`,
`serve.py`, dependency pins, and launch flags. The installable SGLang package
lives in `sglang/python`; build it editable with
`scripts/install_local_sglang.sh`. Serving large MoE models is expert- and
tensor-parallel: set `--tp-size`/`--ep-size`/`--dp-size` in `main.py` to match
the GPU count declared in the bundle `OBJECTIVE.md`.

Evaluator-owned benchmark and accuracy files come from the input bundle and
must not be modified to make a candidate pass.
