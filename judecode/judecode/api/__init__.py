"""API package — provider-agnostic client factory."""

from judecode.config import PROVIDER


def create_api_client():
    """Factory: return the right API client based on JUDECODE_PROVIDER."""
    if PROVIDER == "anthropic":
        from judecode.api.anthropic import AnthropicClient
        from judecode.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MAX_TOKENS, TEMPERATURE
        return AnthropicClient(
            api_key=ANTHROPIC_API_KEY,
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
    elif PROVIDER == "zai":
        # Z.AI / Zhipu GLM (OpenAI-compatible)
        from judecode.api.client import ApiClient
        from judecode.config import ZAI_BASE_URL, ZAI_API_KEY, ZAI_MODEL
        return ApiClient(
            base_url=ZAI_BASE_URL,
            api_key=ZAI_API_KEY,
            model=ZAI_MODEL,
            fallback_model=ZAI_MODEL,  # GLM has no separate non-thinking fallback
        )
    else:
        # Default: DeepSeek / OpenAI-compatible
        from judecode.api.client import ApiClient
        return ApiClient()


# For backward compatibility
from judecode.api.client import ApiClient

__all__ = ["ApiClient", "create_api_client"]
