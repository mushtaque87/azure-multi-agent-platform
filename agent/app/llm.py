"""LLM client for the Azure AI Foundry deployment (OpenAI v1-compatible endpoint)."""
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import ChatOpenAI

from . import config


def get_llm():
    if config.FOUNDRY_API_KEY:
        api_key = config.FOUNDRY_API_KEY
    else:
        # Keyless: managed identity / az login with the "Cognitive Services OpenAI User" role
        api_key = get_bearer_token_provider(DefaultAzureCredential(),
                                            "https://cognitiveservices.azure.com/.default")
    # temperature 0: tool choice and numbers should be as repeatable as possible (helps evaluation)
    return ChatOpenAI(base_url=config.FOUNDRY_BASE_URL, api_key=api_key,
                      model=config.FOUNDRY_MODEL, temperature=0, timeout=60, max_retries=2)
