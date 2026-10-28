"""Settings from environment variables (12-factor app: config lives in the environment, not the code).
Non-secret defaults live here; secrets come from Key Vault (locally passed in by run_agent.sh,
in Azure via Container Apps Key Vault references). os.getenv(name, default) = env value or the default."""
import os

# --- LLM (Azure AI Foundry) ---
FOUNDRY_BASE_URL = os.getenv("FOUNDRY_BASE_URL", "https://ai103-agenticsystem-1.openai.azure.com/openai/v1")
FOUNDRY_MODEL = os.getenv("FOUNDRY_MODEL", "gpt-4.1-mini")  # deployment name
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY")  # unset = use Entra ID (managed identity) instead

# --- Database (Azure SQL) ---
SQL_SERVER = os.getenv("SQL_SERVER", "sql-house-agents.database.windows.net")
SQL_DATABASE = os.getenv("SQL_DATABASE", "housepricedb")

# --- Price model ---
# Azure ML endpoint (from ai-mlops/azure_ml/deploy.py). If unset, predict_price uses the local model copy.
ML_ENDPOINT_URL = os.getenv("ML_ENDPOINT_URL")
ML_ENDPOINT_KEY = os.getenv("ML_ENDPOINT_KEY")
LOCAL_MODEL_DIR = os.getenv("LOCAL_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "model"))

# --- Guardrail limits ---
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "2000"))  # longest user message accepted
SQL_ROW_LIMIT = int(os.getenv("SQL_ROW_LIMIT", "100"))       # TOP n forced on LLM-written SQL

# --- Context and cost management ---
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "6000"))  # history tokens sent per LLM call
MAX_TURNS = int(os.getenv("MAX_TURNS", "30"))                      # user questions per conversation

# --- Tracing ---
# Phoenix OTLP endpoint, e.g. http://phoenix:6006. Unset = tracing off.
PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
