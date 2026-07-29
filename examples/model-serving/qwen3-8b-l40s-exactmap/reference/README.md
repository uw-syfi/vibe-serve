# Pinned Qwen3-8B reference

`meta.json` pins the same Qwen3-8B revision used by the PIQ L40S comparison.
VibeSys materializes its model and tokenizer files into the reference model
directory.

`reference.py` is a small correctness reference. ExactMap candidates may use
Transformers for configuration and tokenizer behavior, but the optimized
serving path must implement model execution itself.
