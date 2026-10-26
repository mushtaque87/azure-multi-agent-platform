# Azure Multi-Agent Platform: house-price AI agents

An Azure-native, multi-agent AI system built step by step:
- a LangGraph orchestrator talking over **A2A** to specialist agents;
- tools exposed through **MCP** servers;
- Azure AI Foundry (gpt-4.1-mini) as the LLM;
- Azure SQL and the Azure ML model behind the tools;
- Azure AI Search for RAG;
- Azure Container Apps for hosting;
- Arize Phoenix for observability;
- automated evaluation, red-teaming and load testing.

The model and its serving infrastructure come from the companion repo **[ai-mlops](https://github.com/mushtaque87/ai-mlops)**, where the house-price model was trained in Snowflake, served on an Azure ML managed online endpoint, and load tested.

## Progress

| Phase | Status |
|---|---|
| 1. Foundation: resource group, budget, Azure SQL (Entra-only, serverless), Key Vault (RBAC) | Done |
| 2. Single LangGraph agent with direct tools | In progress |
| 3. Chat UI, Container Apps, tracing | Planned |
| 4. Evaluation and security baseline | Planned |
| 5. Tools refactored into MCP servers | Planned |
| 6. Multi-agent over A2A plus RAG advisor | Planned |
| 7. APIM AI gateway, full evaluation, load testing | Planned |
| 8. Terraform, CI/CD, ADRs | Planned |

## Phase 1 results

- **Data migrated from Snowflake to Azure SQL:** 10,000 rows. The Downtown average is 770,570 in both.
- **No passwords anywhere:**
  - Azure SQL uses Microsoft Entra-only authentication;
  - scripts connect with a short-lived Entra token from `az login`;
  - secrets live in Key Vault, using the RBAC permission model.
- The SQL scripts run in a Linux tools container with ODBC Driver 18 (`infra/docker/tools.Dockerfile`). The agent image will reuse the same driver setup.

```bash
./infra/scripts/run_tools.sh sql_setup.py -s sql-house-agents --load   # create table, load, verify
```

Concept notes for each phase are in [docs/concepts/](docs/concepts/).
