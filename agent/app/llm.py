"""LLM client for the Azure AI Foundry deployment (OpenAI v1-compatible endpoint)."""
# Keyless auth helpers: get an Entra token and refresh it automatically
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
# ChatOpenAI speaks the OpenAI chat API; Foundry's /openai/v1 endpoint is compatible with it
from langchain_openai import ChatOpenAI

from . import config


def get_llm():
    """Return a chat model client. Creating it is cheap; each .invoke() is one billed API call."""
    if config.FOUNDRY_API_KEY:
        api_key = config.FOUNDRY_API_KEY  # local dev: key read from Key Vault by run_agent.sh
    else:
        # Keyless (production): managed identity / az login with the "Cognitive Services OpenAI User"
        # role. A callable that returns a fresh token is accepted in place of a static key.
        api_key = get_bearer_token_provider(DefaultAzureCredential(),
                                            "https://cognitiveservices.azure.com/.default")
    return ChatOpenAI(
        base_url=config.FOUNDRY_BASE_URL,  # https://<resource>.openai.azure.com/openai/v1
        api_key=api_key,
        model=config.FOUNDRY_MODEL,        # the DEPLOYMENT name in Foundry (gpt-4.1-mini)
        temperature=0,  # least random: tool choice and numbers as repeatable as possible (helps evaluation)
        timeout=60,     # seconds before giving up on one call
        max_retries=2,  # retry transient errors / 429 rate limits with backoff
    )
