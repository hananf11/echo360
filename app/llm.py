"""Shared LiteLLM Router for LLM calls with automatic fallback and cooldowns."""
import logging

from litellm import Router

_LOGGER = logging.getLogger(__name__)

# Model groups for notes generation, in fallback order.
# Each group has multiple deployments the router can cycle through.
# On failure: retries within group → cooldown bad deployments → fallback to next group.
NOTES_FREE = [
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/google/gemma-3-27b-it:free",
    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
    "openrouter/deepseek/deepseek-r1-0528:free",
    "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
]

NOTES_PAID = [
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/google/gemini-2.5-flash-lite",
    "openrouter/minimax/minimax-m2.1",
]

TITLES_MODELS = [
    "openrouter/google/gemini-2.5-flash-lite",
    "openrouter/minimax/minimax-m2.1",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
]


def _build_model_list() -> list[dict]:
    """Build Router model_list.

    - notes-free: multiple free-tier deployments (router cycles through on failure)
    - notes-paid: multiple paid deployments (fallback when all free are exhausted)
    - titles: multiple deployments for title cleanup
    """
    model_list = []

    for i, model in enumerate(NOTES_FREE):
        model_list.append({
            "model_name": "notes-free",
            "litellm_params": {"model": model},
            "model_info": {"id": f"notes-free-{i}"},
        })

    for i, model in enumerate(NOTES_PAID):
        model_list.append({
            "model_name": "notes-paid",
            "litellm_params": {"model": model},
            "model_info": {"id": f"notes-paid-{i}"},
        })

    for i, model in enumerate(TITLES_MODELS):
        model_list.append({
            "model_name": "titles",
            "litellm_params": {"model": model},
            "model_info": {"id": f"titles-{i}"},
        })

    return model_list


router = Router(
    model_list=_build_model_list(),
    # notes-free fails → try notes-paid
    fallbacks=[{"notes-free": ["notes-paid"]}],
    # Within a group: cooldown a deployment after 1 failure, try another deployment
    allowed_fails=1,
    cooldown_time=30,
    # Retry within a group enough times to cycle through all deployments
    num_retries=5,
    retry_after=0,
    # Alias so callers can just use model="notes"
    model_group_alias={"notes": "notes-free"},
)
