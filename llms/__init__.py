"""LLM provider registry and factory."""

from typing import Callable

from llms.vertexai import get_vertexai_llm

LLM_REGISTRY: dict[str, Callable] = {
    "vertexai": get_vertexai_llm,
}


def get_llm(provider: str, model: str, **kwargs) -> Callable:
    """Build and return an LLM callable for the given provider.

    Returns a ``generate_json(prompt, system_prompt, schema) -> dict`` closure.
    """
    builder = LLM_REGISTRY.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Available: {', '.join(LLM_REGISTRY)}"
        )
    return builder(model, **kwargs)
