import json  # noqa: D100  # tracked: #288
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from vibesys.config import Config, ThinkingCfg
from vibesys.constants import (
    ANTHROPIC_PREFIXES,
    GOOGLE_PREFIXES,
    OPENAI_PREFIXES,
)


def _is_google_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in GOOGLE_PREFIXES)


def _is_anthropic_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in ANTHROPIC_PREFIXES)


def _is_openai_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in OPENAI_PREFIXES)


def _has_thinking(thinking: ThinkingCfg) -> bool:
    return thinking.level is not None or thinking.budget is not None


def build_model(config: Config):  # noqa: ANN201  # tracked: #288
    """Build the chat model from a parsed :class:`Config`."""
    model_name = config.model.name
    provider = config.model.provider
    thinking = config.thinking

    if provider == "vertex-ai":
        return _build_vertex_model(model_name, config, thinking)

    if provider == "anthropic":
        if not _is_anthropic_model(model_name):
            raise ValueError(f"{model_name!r} is not a Claude model (provider='anthropic')")  # noqa: TRY003  # tracked: #288
        if _has_thinking(thinking):
            raise ValueError("Thinking is not supported for provider 'anthropic'")  # noqa: TRY003  # tracked: #288
        return f"anthropic:{model_name}"

    if provider == "google-genai":
        if not _is_google_model(model_name):
            raise ValueError(f"{model_name!r} is not a Google model (provider='google-genai')")  # noqa: TRY003  # tracked: #288
        if _has_thinking(thinking):
            raise ValueError("Thinking is not supported for provider 'google-genai'")  # noqa: TRY003  # tracked: #288
        return f"google_genai:{model_name}"

    if provider == "openai":
        if not _is_openai_model(model_name):
            raise ValueError(f"{model_name!r} is not an OpenAI model (provider='openai')")  # noqa: TRY003  # tracked: #288
        if _has_thinking(thinking):
            raise ValueError("Thinking is not supported for provider 'openai'")  # noqa: TRY003  # tracked: #288
        return f"openai:{model_name}"

    if provider == "openai-compatible":
        return _build_openai_compatible_model(model_name, config)

    if provider is None:
        # Auto-detect from model name
        if _is_anthropic_model(model_name):
            if _has_thinking(thinking):
                raise ValueError("Thinking is not supported for provider 'anthropic'")  # noqa: TRY003  # tracked: #288
            return f"anthropic:{model_name}"
        if _is_google_model(model_name):
            if _has_thinking(thinking):
                raise ValueError("Thinking is not supported for provider 'google-genai'")  # noqa: TRY003  # tracked: #288
            return f"google_genai:{model_name}"
        if _is_openai_model(model_name):
            if _has_thinking(thinking):
                raise ValueError("Thinking is not supported for provider 'openai'")  # noqa: TRY003  # tracked: #288
            return f"openai:{model_name}"
        raise ValueError(  # noqa: TRY003  # tracked: #288
            f"Cannot auto-detect provider for model {model_name!r}. "
            f"Set model.provider in your config."
        )

    raise NotImplementedError(f"Provider {provider!r} is not yet supported")


def _build_openai_compatible_model(model_name: str, config: Config):  # noqa: ANN202  # tracked: #288
    """Build a model using an OpenAI-compatible API (e.g. vLLM, Ollama)."""
    from langchain_openai import ChatOpenAI  # noqa: PLC0415  # tracked: #288

    oc = config.providers.openai_compatible
    if oc is None or not oc.base_url:
        raise ValueError(  # noqa: TRY003  # tracked: #288
            "openai-compatible provider requires 'base_url' (e.g. 'http://localhost:8000/v1')"
        )

    return ChatOpenAI(
        model=model_name,
        base_url=oc.base_url,
        api_key=SecretStr(oc.api_key),
    )


def _build_vertex_model(model_name: str, config: Config, thinking: ThinkingCfg):  # noqa: ANN202  # tracked: #288
    """Build a Vertex AI model (Claude via Model Garden or Gemini via GenAI)."""
    from google.oauth2 import service_account  # noqa: PLC0415  # tracked: #288

    vx = config.providers.vertex_ai
    vertex_json = vx.json_path if vx else None
    vertex_project = vx.project if vx else None
    vertex_region = vx.region if vx else "us-east5"

    if not vertex_json:
        raise ValueError("vertex-ai provider requires 'json' key path")  # noqa: TRY003  # tracked: #288

    key_path = Path(vertex_json).expanduser()
    if not key_path.exists():
        raise ValueError(f"Vertex AI service account key not found: {key_path}")  # noqa: TRY003  # tracked: #288

    creds_dict = json.loads(key_path.read_text())
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    project = vertex_project or creds_dict.get("project_id")

    if not _is_google_model(model_name) and _has_thinking(thinking):
        raise ValueError("Thinking is not supported for non-Gemini models on Vertex AI")  # noqa: TRY003  # tracked: #288

    if _is_google_model(model_name):
        thinking_kwargs = {}
        thinking_level = thinking.level
        thinking_budget = thinking.budget
        if thinking_level is not None:
            thinking_kwargs["thinking_level"] = thinking_level
            thinking_kwargs["include_thoughts"] = True
        elif thinking_budget is not None:
            thinking_kwargs["thinking_budget"] = thinking_budget
            thinking_kwargs["include_thoughts"] = True

        return _vertex_gemini_model_class()(
            model=model_name,
            credentials=credentials,
            project=project,
            location=vertex_region,
            **thinking_kwargs,
        )
    return _vertex_anthropic_model_class()(
        model_name=model_name,
        credentials=credentials,
        project=project,
        location=vertex_region,
    )


def _vertex_anthropic_model_class() -> Any:  # noqa: ANN401  # tracked: #288
    """Load the optional Vertex Anthropic class only when it is needed."""
    from langchain_google_vertexai.model_garden import (  # noqa: PLC0415  # tracked: #288
        ChatAnthropicVertex,
    )

    return ChatAnthropicVertex


def _vertex_gemini_model_class() -> Any:  # noqa: ANN401  # tracked: #288
    """Load the optional Vertex Gemini class only when it is needed."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415  # tracked: #288

    return ChatGoogleGenerativeAI
