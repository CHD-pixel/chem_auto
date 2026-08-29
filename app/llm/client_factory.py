import os

from google.adk.models.lite_llm import LiteLlm
from dotenv import load_dotenv

from app.runtime.config import MODEL_PROVIDER_CONFIG

load_dotenv()


def build_llm(model_name: str) -> LiteLlm:
    """Build a LiteLlm model instance with provider config from MODEL_PROVIDER_CONFIG.

    For DeepSeek models, automatically passes ``extra_body`` to disable thinking
    mode (DeepSeek V4 defaults to thinking=enabled).  Google GenAI's
    ``ThinkingLevel`` enum is not used because it does not map to DeepSeek's
    ``{"thinking": {"type": "disabled"}}`` mechanism.
    """
    config = MODEL_PROVIDER_CONFIG.get(model_name)
    if config is None:
        raise ValueError(f"Unknown model: {model_name}")

    api_key = os.getenv(config["api_key_env"], "").strip()
    api_base = os.getenv(config["api_base_env"], config["api_base_default"]).strip()

    if not api_key:
        raise ValueError(
            f"Missing API key for model {model_name}: set {config['api_key_env']} in the environment."
        )

    # DeepSeek API uses deepseek/ prefix to avoid ADK's OpenAI file-upload path
    # (_FILE_ID_REQUIRED_PROVIDERS = {"openai", "azure"}).
    extra_kwargs: dict = {}
    if "deepseek.com" in api_base:
        # DeepSeek V4 defaults to thinking=enabled. Disable it — Google GenAI's
        # ThinkingLevel enum does not map to DeepSeek's mechanism.
        extra_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    return LiteLlm(
        model=f"deepseek/{model_name}",
        api_key=api_key,
        api_base=api_base,
        temperature=0,
        **extra_kwargs,
    )
