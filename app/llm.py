"""Shared LiteLLM Router for LLM calls with automatic fallback and cooldowns."""
import logging

from litellm import Router

_LOGGER = logging.getLogger(__name__)

# Ordered model chains — each becomes a separate Router deployment group
# so that fallbacks proceed in this exact order.
NOTES_MODELS = [
    # Free tier
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/google/gemma-3-27b-it:free",
    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
    "openrouter/deepseek/deepseek-r1-0528:free",
    "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
    # Paid, sorted by price
    "openrouter/meta-llama/llama-3.3-70b-instruct",        # $0.10/$0.32
    "openrouter/google/gemini-2.5-flash-lite",              # $0.10/$0.40
    "openrouter/minimax/minimax-m2.1",                      # $0.27/$0.95
]

TITLES_MODELS = [
    "openrouter/minimax/minimax-m2.1",
]


def _build_model_list() -> list[dict]:
    """Build Router model_list with one deployment per model per group."""
    model_list = []
    for i, model in enumerate(NOTES_MODELS):
        model_list.append({
            "model_name": f"notes-{i}",
            "litellm_params": {"model": model},
        })
    for i, model in enumerate(TITLES_MODELS):
        model_list.append({
            "model_name": f"titles-{i}",
            "litellm_params": {"model": model},
        })
    return model_list


def _build_fallbacks() -> list[dict]:
    """Build fallback chain: notes-0 → notes-1 → ... → notes-N."""
    fallbacks = []
    if len(NOTES_MODELS) > 1:
        fallbacks.append({"notes-0": [f"notes-{i}" for i in range(1, len(NOTES_MODELS))]})
    return fallbacks


router = Router(
    model_list=_build_model_list(),
    fallbacks=_build_fallbacks(),
    model_group_alias={"notes": "notes-0", "titles": "titles-0"},
    allowed_fails=1,
    cooldown_time=60,
    num_retries=2,  # Retry each model up to 2 times (respects Retry-After headers, exponential backoff otherwise)
    retry_after=0,
)
