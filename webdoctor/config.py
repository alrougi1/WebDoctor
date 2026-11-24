"""Default configuration for WebDoctor."""
import os
from importlib.metadata import PackageNotFoundError, version as _package_version
from typing import Optional

try:
    TOOL_VERSION = _package_version("webdoctor")
except PackageNotFoundError:
    from webdoctor.version import __version__ as TOOL_VERSION

DEFAULT_TIMEOUT = int(os.environ.get("WEBDOCTOR_TIMEOUT", "20"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "WebDoctor/1.0"
)
MAX_LINKS_TO_CHECK = 40
LINK_CHECK_CONCURRENCY = 8
MAX_PAGE_WAIT_MS = 25000

# Deterministic hard monkey test defaults. A fixed seed makes every run
# reproducible; the limits keep a single run bounded and safe.
MONKEY_DEFAULT_SEED = 20261124
MONKEY_DEFAULT_ACTIONS = 200
MONKEY_MIN_ACTIONS = 1
MONKEY_MAX_ACTIONS = 500
MONKEY_DEFAULT_DELAY_MS = 60
MONKEY_MAX_DELAY_MS = 5000

# Verified against the providers' public model catalogs on 2026-11-24.
AI_MODEL_FAST = "gpt-6-luna"           # sandbox / economical default
AI_MODEL_DEEP = "gpt-6.1-sol"          # sandbox / deeper reasoning
AI_MODEL_OPENAI = "gpt-6.2-sol"        # OpenAI direct: everyday work
AI_MODEL_ANTHROPIC = "claude-sonnet-5-5"  # Anthropic: speed + intelligence
AI_MODEL_GEMINI = "gemini-4-hydrogen"  # Gemini: efficient everyday reasoning

# Environment variables used when --ai-key is not passed explicitly.
API_KEY_ENV_VARS = {
    "sandbox": "OPENAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def default_model_for_provider(provider: str) -> str:
    """Return the verified default model ID for a provider."""
    provider = (provider or "sandbox").strip().lower()
    defaults = {
        "sandbox": AI_MODEL_FAST,
        "openai": AI_MODEL_OPENAI,
        "anthropic": AI_MODEL_ANTHROPIC,
        "claude": AI_MODEL_ANTHROPIC,
        "gemini": AI_MODEL_GEMINI,
    }
    if provider not in defaults:
        raise ValueError(f"Unknown AI provider '{provider}'. Use sandbox|openai|anthropic|gemini.")
    return defaults[provider]


def provider_credentials_available(provider: str, api_key: Optional[str] = None,
                                   api_base: Optional[str] = None) -> bool:
    """Whether a provider can be called with the available configuration.

    A custom OpenAI-compatible base URL counts as configuration for the
    sandbox/OpenAI providers (used by managed environments and proxies).
    """
    provider = (provider or "sandbox").strip().lower()
    if api_key:
        return True
    env_var = API_KEY_ENV_VARS.get(provider)
    if env_var and os.getenv(env_var):
        return True
    if provider in ("sandbox", "openai"):
        return bool(api_base or os.getenv("OPENAI_BASE_URL"))
    return False
