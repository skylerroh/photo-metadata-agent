import os

from photo_metadata_agent.inference.contracts import ProviderError, Tagger
from photo_metadata_agent.inference.providers.chat import ChatTagger
from photo_metadata_agent.inference.providers.demo import DemoTagger
from photo_metadata_agent.inference.providers.typesafe import TypeSafeTagger


def create_tagger(spec: dict) -> Tagger:
    provider = spec["provider"]
    if provider == "demo":
        return DemoTagger()
    if provider == "typesafe":
        if not spec["typesafe_images_enabled"]:
            raise ProviderError(
                "Enable PHOTO_AGENT_TYPESAFE_IMAGES_ENABLED for experimental images."
            )
        key_name = "TYPESAFE_API_KEY"
    elif provider in ("openai", "openrouter"):
        key_name = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}[provider]
    elif provider == "ollama":
        return ChatTagger(
            provider,
            spec["ollama_url"].rstrip("/") + "/api/chat",
            "",
            spec["timeout_seconds"],
            spec["max_attempts"],
        )
    else:
        raise ProviderError("Unknown provider.")
    # Read .env keys without persisting them in the job specification.
    from dotenv import dotenv_values

    key = os.environ.get(key_name) or dotenv_values(".env").get(key_name)
    if not key:
        raise ProviderError(f"Set {key_name} before running this provider.")
    if provider == "typesafe":
        return TypeSafeTagger(key, spec["timeout_seconds"], spec["max_attempts"])
    url = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    }[provider]
    return ChatTagger(provider, url, key, spec["timeout_seconds"], spec["max_attempts"])
