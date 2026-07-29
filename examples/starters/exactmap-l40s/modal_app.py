from __future__ import annotations

import modal

app = modal.App("vibesys-exactmap-l40s-candidate")

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("git")
    .pip_install(
        "accelerate>=1.2",
        "fastapi>=0.115",
        "huggingface-hub>=0.30",
        "safetensors>=0.5",
        "torch>=2.7",
        "transformers==4.53.2",
        "triton>=3.3",
        "uvicorn>=0.30",
    )
)


@app.function(image=image, gpu="L40S", timeout=7200, cpu=8.0, memory=65536)
def smoke() -> str:
    return "exactmap-l40s starter ready"
