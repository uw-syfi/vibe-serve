from __future__ import annotations

import argparse
import os

from exactmap.api import create_app
from exactmap.bootstrap import BootstrapTransformersEngine
from exactmap.config import EngineConfig
from exactmap.kernel_engine import ExactMapKernelEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ExactMap candidate server.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument(
        "--engine",
        choices=("kernel", "bootstrap"),
        default=os.environ.get("EXACTMAP_ENGINE", "kernel"),
    )
    parser.add_argument("--model-path", default=os.environ.get("MODEL_PATH", "Qwen/Qwen3-8B"))
    parser.add_argument("--tokenizer-path", default=os.environ.get("TOKENIZER_PATH"))
    parser.add_argument("--max-model-len", type=int, default=16_384)
    parser.add_argument("--max-batch-size", type=int, default=16)
    parser.add_argument("--max-num-batched-tokens", type=int, default=147_456)
    parser.add_argument("--kv-block-size", type=int, default=16)
    parser.add_argument("--chunked-prefill-size", type=int, default=0)
    parser.add_argument("--engine-build-sha256", default=os.environ.get("ENGINE_BUILD_SHA256"))
    parser.add_argument("--artifact-locator", default=os.environ.get("ARTIFACT_LOCATOR"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = EngineConfig(
        model_path=args.model_path,
        tokenizer_path=args.tokenizer_path,
        max_model_len=args.max_model_len,
        max_batch_size=args.max_batch_size,
        max_num_batched_tokens=args.max_num_batched_tokens,
        kv_block_size=args.kv_block_size,
        chunked_prefill_size=args.chunked_prefill_size,
        engine_build_sha256=args.engine_build_sha256,
        artifact_locator=args.artifact_locator,
        kernel_family=(
            "exactmap-triton-v1" if args.engine == "kernel" else "bootstrap-transformers"
        ),
    )
    if args.engine == "kernel":
        engine = ExactMapKernelEngine(config)
    else:
        engine = BootstrapTransformersEngine(config)
    app = create_app(engine, config)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
