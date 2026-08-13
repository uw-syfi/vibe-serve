from __future__ import annotations

import modal

app = modal.App("vibesys-sglang-b200-candidate")

# CUDA 12.8 devel is required to build/run SGLang kernels for Blackwell (sm_100).
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("git")
    .pip_install(
        "sglang[all]==0.5.17",
        "transformers>=4.53.2",
        "httpx>=0.27",
        "jsonschema>=4.0",
    )
)


@app.function(
    image=image,
    gpu="B200!",
    timeout=7200,
    cpu=8.0,
    memory=65536,
)
def smoke() -> str:
    return "sglang-b200 starter ready"
