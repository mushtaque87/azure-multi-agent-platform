"""Settings from environment variables (12-factor). Non-secret names live here; secrets come from
Key Vault (locally passed in by run_agent.sh, in Azure via Container Apps Key Vault references)."""
import os

FOUNDRY_BASE_URL = os.getenv("FOUNDRY_BASE_URL", "https://ai103-agenticsystem-1.openai.azure.com/openai/v1")
FOUNDRY_MODEL = os.getenv("FOUNDRY_MODEL", "gpt-4.1-mini")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY")  # empty = use Entra ID (managed identity) instead

SQL_SERVER = os.getenv("SQL_SERVER", "sql-house-agents.database.windows.net")
SQL_DATABASE = os.getenv("SQL_DATABASE", "housepricedb")

# Azure ML endpoint (from ai-mlops/azure_ml/deploy.py). If unset, predict_price uses the local model copy.
ML_ENDPOINT_URL = os.getenv("ML_ENDPOINT_URL")
ML_ENDPOINT_KEY = os.getenv("ML_ENDPOINT_KEY")
LOCAL_MODEL_DIR = os.getenv("LOCAL_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "model"))

MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "2000"))
SQL_ROW_LIMIT = int(os.getenv("SQL_ROW_LIMIT", "100"))
