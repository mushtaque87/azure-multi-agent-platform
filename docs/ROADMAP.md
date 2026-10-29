# Project roadmap

Status is kept here so the project can be continued in any tool or editor: everything needed is in this repo and in the Azure subscription.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: resource group, budget, Azure SQL (Entra-only, serverless), Key Vault (RBAC) | Done |
| 2 | Single LangGraph agent with 5 guarded tools; 5/5 parity with the Snowflake Cortex Agent | Done |
| 3 | Chat UI and Phoenix tracing (done locally) → Azure Container Apps (no VNet), managed identity, read-only SQL user | Done |
| 4 | **Multi-agent over A2A**: supervisor plus specialist agents (valuation, market data); Phoenix deployed to Container Apps for cloud tracing | In progress |
| 5 | Evaluation and security baseline: JSONL golden set (incl. supervisor routing accuracy), deterministic checks plus LLM-as-judge, Prompt Shields, red team | Planned |
| 6 | APIM AI gateway: token limits and metrics per app, managed identity to Foundry, load test | Planned |
| 7 | Fine-tuning by distillation: gpt-4.1-nano trained on gpt-4.1-mini traces, evaluation-gated | Planned |
| 8 | RAG advisor (Azure AI Search + embeddings), semantic cache, hit-rate and $-saved dashboard | Planned |
| 9 | Tools refactored into MCP servers | Planned |
| 10 | Terraform, CI/CD with the evaluation gate, ADRs and documentation | Planned |

Moved to a separate networking project: VNet-integrated Container Apps environment, SQL private endpoint, private DNS, public access disabled.

## Design notes captured for later phases

**Phase 6, APIM.**
- Each app authenticates to APIM with its own managed identity or a subscription key.
- APIM calls Foundry with *its* managed identity.
- `azure-openai-token-limit` and `azure-openai-emit-token-metric` give per-app quotas and cost reporting.
- The `azure-openai-semantic-cache-*` policies are the gateway-level alternative to the app-level cache in Phase 8.

**Phase 7, fine-tuning.**
1. Collect correct gpt-4.1-mini tool calls and SQL from Phoenix traces as SFT data.
2. Fine-tune gpt-4.1-nano in Foundry.
3. Run the Phase 4 golden set against both.
4. Adopt nano for a specialist only if it passes the quality gate.

Check training and hosting prices first. Fine-tuned deployments can bill hourly; use the Developer deployment type and delete after evaluating.

**Phase 8, semantic cache.**
- Embed the question and look up similar past questions in AI Search. A hit at similarity ≥ 0.95 returns the cached answer; a miss runs the agent and stores the answer.
- Only cache standalone first-turn questions.
- Require the extracted numbers to match, set a TTL, and clear the cache when the data reloads.
- Never cache refusals or errors.
- Record `cache.hit`, the tokens avoided and the $ saved as span attributes and App Insights metrics, and show them in an Azure Monitor Workbook.

## Azure resources (all in `rg-house-agents`, UAE North, budget ₹1,000)

| Resource | Name |
|---|---|
| SQL server / database | `sql-house-agents` / `housepricedb` (GP serverless, auto-pause 15 min, Entra-only) |
| Key Vault | `kv-house-agents` (RBAC; secret `foundry-api-key`) |
| Foundry (other RG `rg_az_ai103`, eastus) | `ai103-agenticsystem-1`, deployments `gpt-4.1-mini`, `gpt-5.4` (GlobalStandard, pay per token) |
| Container Apps | `cae-house-agents` / `acrhouseagents` (Phase 3) |

The SQL firewall rule `my-ip` must match your current home IP for local development; it changes whenever the router reconnects.
