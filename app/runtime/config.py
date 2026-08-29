import os

MULTIMODAL_MODEL = "deepseek-v4-pro"
TEXT_MODEL = "deepseek-v4-pro"

MODEL_PROVIDER_CONFIG = {
    MULTIMODAL_MODEL: {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_base_env": "DEEPSEEK_BASE_URL",
        "api_base_default": "https://api.deepseek.com",
    },
}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

CHEMAUTO_REQUIRE_USER_CONFIRMATION = _env_flag("CHEMAUTO_REQUIRE_USER_CONFIRMATION", True)
