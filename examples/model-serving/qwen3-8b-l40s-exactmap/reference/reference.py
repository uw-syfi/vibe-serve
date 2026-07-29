from __future__ import annotations

from typing import Any

MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"


def load_reference(model_path: str = MODEL_ID) -> tuple[Any, Any]:
    """Load the pinned Hugging Face reference for offline correctness work."""

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        revision=MODEL_REVISION,
        trust_remote_code=False,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        revision=MODEL_REVISION,
        torch_dtype=torch.bfloat16,
        trust_remote_code=False,
    )
    model.eval()
    return tokenizer, model


def greedy_chat_tokens(
    tokenizer: Any,
    model: Any,
    messages: list[dict[str, str]],
    *,
    max_new_tokens: int,
    enable_thinking: bool = True,
) -> list[int]:
    """Return deterministic reference token ids for a chat request."""

    import torch

    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=enable_thinking,
    )
    encoded = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
    with torch.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
    return output[0, encoded["input_ids"].shape[1] :].tolist()
