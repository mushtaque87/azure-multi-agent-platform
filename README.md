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
| 2. Single LangGraph agent with direct tools | Done |
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

Concept notes for each phase are in [docs/concepts/](docs/concepts/). Guardrail principles: [docs/concepts/guardrails.md](docs/concepts/guardrails.md).

## Phase 2 results: single LangGraph agent

```mermaid
flowchart LR
  user_node[Client] -->|POST /chat| api[FastAPI]
  api --> guard[guard_input]
  guard --> llm["agent: gpt-4.1-mini (Foundry)"]
  llm <--> tools[ToolNode]
  tools --> sql[(Azure SQL)]
  tools --> ml[Azure ML endpoint or local model]
```

**Tools:**
- `predict_price`
- `explain_prediction`
- `lookup_house` (parameterised SQL)
- `query_house_stats` (text-to-SQL behind a `sqlglot` validator)
- `compare_to_market` (prediction plus similar sales)

**Evaluation:** the same 5 questions used for the Snowflake Cortex Agent, all passing with identical numbers.

| Question | Tool | Answer | Latency |
|---|---|---|---|
| Avg price Downtown | query_house_stats | $770,570 | 4.2 s |
| Houses with a pool | query_house_stats | 5,018 | 2.5 s |
| House ID 42 | lookup_house | $566,563 | 2.6 s |
| Predict Suburban 2000 sqft | predict_price | $522,171 | 3.8 s |
| Predict Downtown 2500 sqft | predict_price | $784,960 | 2.4 s |

**Behaviour checks, all passing:**
- multi-step market comparison;
- follow-up questions using thread memory;
- asks for missing features instead of guessing;
- declines off-topic requests;
- prompt injection blocked before any LLM call (2 ms);
- write requests refused.

**Guard unit tests:** DELETE, UPDATE, DROP, stacked queries, SELECT INTO, `sys.*` tables, `xp_cmdshell` and `OPENROWSET` are all rejected.

A bug found by evaluation and fixed in the guard: the LLM wrote `CAST(AVG(price) AS FLOAT)`. T-SQL averages INT columns with integer division, which gave 770,569. The validator now rewrites every `AVG(x)` to `AVG(CAST(x AS FLOAT))`, a deterministic fix that doesn't depend on the prompt.

```bash
./agent/run_agent.sh                                         # build + run on :8000 (secrets from Key Vault / az)
docker exec -w /srv house-agent python -m tests.test_guards  # guard unit tests
```

Try it with [api/requests.http](api/requests.http).
