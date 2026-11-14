# Project roadmap

Status is kept here so the project can be continued in any tool or editor: everything needed is in this repo and in the Azure subscription.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: resource group, budget, Azure SQL (Entra-only, serverless), Key Vault (RBAC) | Done |
| 2 | Single LangGraph agent with 5 guarded tools; 5/5 parity with the Snowflake Cortex Agent | Done |
| 3 | Chat UI and Phoenix tracing → Azure Container Apps (no VNet), managed identity, read-only SQL user | Done |
| 4 | **Multi-agent over A2A**: supervisor + valuation + market agents; per-agent identities; internal-only specialists; Phoenix in Azure behind login; `list_houses` (render, don't read) | Done |
| 5 | **Evaluation, security and hallucination control**: 5a golden set + runner (done, 30/30 local) · 5b Prompt Shields · 5c red team · 5d runtime hallucination control · 5e prompt versioning + A/B | In progress |
| 6 | **Human-in-the-loop, exceptions and escalation** (approve/reject, escalate to a human) | Planned (new) |
| 7 | **Document processing / OCR** (highly preferred): extract house features from property documents | Planned (new) |
| 8 | APIM AI gateway: token limits and metrics per app, managed identity to Foundry, load test; complexity-based model routing, streaming | Planned |
| 9 | Model selection: fine-tuned gpt-4.1-nano (distillation) and an open model (Llama via Foundry serverless) compared using the test set | Planned |
| 10 | RAG over product docs **and conversation history** (AI Search hybrid + re-ranking; FAISS baseline compared), semantic cache, $-saved dashboard | Planned |
| 11 | Tools refactored into MCP servers | Planned |
| 12 | Terraform, CI/CD with the evaluation gate, App Insights alerts, model card / responsible-AI notes, ADRs | Planned |

Moved to a separate networking project: VNet-integrated Container Apps environment, SQL private endpoint, private DNS, public access disabled.

## Job-description alignment (AI engineer / solutions architect)

| JD requirement | Where it's demonstrated |
|---|---|
| GenAI apps in Python; GPT and Llama | Phases 2–4; open-model comparison in 9 |
| Prompt engineering, test, optimise | Prompts + test set (5a); prompt versioning and A/B (5e) |
| RAG over conversations and product docs | Phase 10 |
| Vector DB (Pinecone/Weaviate/FAISS) | Phase 10: FAISS baseline vs Azure AI Search, behind one retriever interface |
| Orchestration, model selection, inference optimisation | Phase 4 (LangGraph, A2A); 8 (routing, streaming); 9; 10 (cache) |
| Testing, monitoring, documentation, compliance | Phase 5, Phoenix, guardrail inventory; 12 (alerts, model card) |
| Azure OpenAI, Cognitive Services, AI Search | Foundry; Content Safety Prompt Shields (5b); Document Intelligence (7); AI Search (10) |

## Capability gaps being closed

| Capability | What exists today | Gap | Phase |
|---|---|---|---|
| **HITL, exceptions and escalation** | Retries with backoff (SQL cold start, LLM), graceful tool failures, asking for missing info, recursion limit with a friendly reply | No approve/reject step and no escalation to a human | 6 |
| **OCR / document processing** | None | The main gap: the agents only read structured SQL data | 7 |
| **Hallucination control** | Numbers only from tools (prompt rule); temperature 0; deterministic SQL guards (AVG fix); structured tools; render-don't-read; **offline** groundedness evaluation (21/21) | No **runtime** check: an ungrounded answer would still reach the user; no citations; no abstain policy | 5d |

## Design notes captured for later phases

**Phase 5d, runtime hallucination control.**
- **Grounding check before the answer is returned:**
  - deterministic first: every number in the final answer must appear in (or be a rounding of) a tool result from this turn;
  - an optional LLM groundedness check (Azure AI Content Safety *groundedness detection*, or a judge model) for the rest.
- **On failure:** regenerate once with "use only the tool results", otherwise **abstain**: "I couldn't verify that; here is the raw data".
- **Citations:** each number in the answer is tied to its source (tool, query, house ID); the UI shows "source: lookup_house(42)".
- **Measure it:** groundedness becomes a gating metric in the golden set (currently reported only), plus adversarial cases that tempt invention ("what will prices be in 2030?", "house 0").

**Phase 6, human-in-the-loop, exceptions and escalation.**
- **Approve/reject:**
  - Introduce one *write* action that needs approval, e.g. "flag a sale record as incorrect" or "request a formal valuation report".
  - The agent drafts the request; **LangGraph `interrupt()`** pauses the graph.
  - A reviewer approves or rejects through a `/approvals` API and UI; the graph resumes from its checkpoint.
  - Approved writes go through a stored procedure with EXECUTE permission only (no table write rights).
- **Escalation:** automatic handoff to a human when:
  - the user asks for one;
  - confidence is low or the grounding check fails twice;
  - a tool fails repeatedly;
  - a policy topic comes up (legal, mortgage advice).

  The human receives a summary + trace link; the user is told what happens next.
- **Exceptions:** a typed error taxonomy (transient, user-fixable, permanent, policy), retry only transient ones, circuit breaker on a failing specialist, and specialist timeout → partial answer + an explanation.
- **Persistence:** interrupts need a **persistent checkpointer** (Postgres/Redis/Cosmos), which also unblocks multi-replica scaling.
- **Evaluation cases:** approval flow, rejection flow, an escalation trigger, an approval timeout.

**Phase 7, document processing / OCR.**
- **Use case:**
  1. The user uploads a property document (listing brochure, inspection report, valuation PDF or scanned image).
  2. A new **document agent** extracts the house features.
  3. The valuation agent predicts the price, and the market agent compares it with similar sales.

  Example: "Here's the brochure, is the asking price fair?"
- **Azure service:** Azure AI **Document Intelligence** (prebuilt layout/read OCR plus a custom extraction model) or **Content Understanding** (check the docs for current features and region support), with keyless access via managed identity.
- **Pipeline:**
  1. Upload to Blob Storage.
  2. OCR + field extraction (sqft, bedrooms, bathrooms, age, garage, neighborhood, condition, pool, lot size, asking price), with a **confidence score per field**.
  3. Validation with the existing Pydantic `HouseFeatures` ranges.
  4. Low-confidence or missing fields go back to the user (HITL from Phase 6).
- **A2A:** the document agent is a new specialist with its own Agent Card, so the supervisor discovers it automatically.
- **Evaluation:** a small set of synthetic PDFs and images with known field values; measure field-level extraction accuracy, end-to-end price correctness, and handling of low-quality scans.
- **Security:** file type and size limits, malware scanning (Defender for Storage), and **prompt-injection checks on extracted text** (Prompt Shields document mode).

**Phase 8, APIM.**
- Each app authenticates to APIM with its own managed identity or a subscription key.
- APIM calls Foundry with *its* managed identity.
- `azure-openai-token-limit` and `azure-openai-emit-token-metric` give per-app quotas and cost reporting.
- The `azure-openai-semantic-cache-*` policies are the gateway-level alternative to the app-level cache in Phase 10.

**Phase 9, fine-tuning.**
1. Collect correct gpt-4.1-mini tool calls and SQL from Phoenix traces as SFT data.
2. Fine-tune gpt-4.1-nano in Foundry.
3. Run the golden set against both.
4. Adopt nano for a specialist only if it passes the quality gate.

Check training and hosting prices first. Fine-tuned deployments can bill hourly; use the Developer deployment type and delete after evaluating.

**Phase 10, semantic cache.**
- Embed the question and look up similar past questions in AI Search. A hit at similarity ≥ 0.95 returns the cached answer; a miss runs the agent and stores the answer.
- Only cache standalone first-turn questions.
- Require the extracted numbers to match, set a TTL, and clear the cache when the data reloads.
- Never cache refusals or errors.
- Record `cache.hit`, the tokens avoided and the $ saved as span attributes and App Insights metrics, and show them in an Azure Monitor Workbook.

## Azure resources

All in `rg-house-agents`, UAE North, budget ₹1,000, unless noted.

| Resource | Name |
|---|---|
| SQL server / database | `sql-house-agents` / `housepricedb` (GP serverless, auto-pause 15 min, Entra-only); DB users `acrhouseagents`, `ca-valuation`, `ca-market` (`db_datareader`) |
| Key Vault | `kv-house-agents` (RBAC); secrets `foundry-api-key`, `phoenix-secret`, `phoenix-admin-password`, `phoenix-api-key` |
| Foundry (other RG `rg_az_ai103`, East US) | `ai103-agenticsystem-1`; deployments `gpt-4.1-mini` (agents), `gpt-5.4` (evaluation judge); GlobalStandard, pay per token |
| Content Safety | `cs-house-agents` (Prompt Shields; Entra auth, Cognitive Services User role) |
| Container Apps environment | `cae-house-agents` (Consumption, no VNet) |
| Container Apps | `acrhouseagents` (supervisor + chat UI, public), `ca-valuation` + `ca-market` (internal only), `ca-phoenix` (public, login, min 1 replica) |
| Image | `docker.io/mushtaque87/house-agent:v2` (one image, role by `AGENT_ROLE`) |

**Local development:** `./agent/run_agent.sh` starts the stack and a background refresher (every 2 min) that keeps the SQL token file and the `my-ip` firewall rule in sync with your Mac.
