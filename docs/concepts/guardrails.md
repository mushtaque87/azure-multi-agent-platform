# Guardrails: principles and how this project applies them

Use this page for interview preparation and architecture reviews.

**One-line answer:**
> "Generic guardrails at the gateway and in a shared library; agent-specific guardrails at the tool
> boundary; least-privilege identity as the backstop; and evaluation and tracing to prove they work."

## Defence in depth: 9 layers

No single guard is reliable, especially the ones that rely on the LLM obeying. Stack independent layers, so one failing doesn't matter.

| # | Layer | In this project | Deterministic? |
|---|---|---|---|
| 1 | API schema | `ChatRequest` (Pydantic, max length) | Yes |
| 2 | Input guard | `guards.check_input` regex; **Prompt Shields** (Phase 4) | Regex yes, Shields ML |
| 3 | System prompt | `SYSTEM_PROMPT` rules | **No**: the weakest layer |
| 4 | Tool arguments | Pydantic `HouseFeatures` ranges, `Literal` enums | Yes |
| 5 | Tool-specific guard | `guards.validate_sql`; parameterised `lookup_house` | Yes |
| 6 | Least-privilege identity | Managed identity with `db_datareader` only (Phase 3) | Yes: the strongest |
| 7 | Output guard | Content Safety plus PII detection on answers (Phase 4) | ML |
| 8 | Limits | `recursion_limit`, 15 s query timeout, `TOP 100`, APIM rate and token limits (Phase 7) | Yes |
| 9 | Observe | Phoenix traces, evaluation suite, alerts on blocks | Detects, doesn't prevent |

"ML" means the check is a trained classifier model (Azure AI Content Safety), not a fixed rule. It catches paraphrases that rules miss, but it is probabilistic: it can occasionally miss attacks or block a harmless message.

## Eight principles

1. **Never rely on the prompt alone.** Anything that must not happen gets a code check or a permission.
2. **Prefer deterministic checks.** Code and permissions behave the same every time; LLM judgement doesn't. Example: the `AVG` integer-division fix is a rewrite in code, not a hint in the prompt.
3. **Guard at the boundary where damage happens.** The SQL validator sits immediately before the database, whatever the LLM was tricked into.
4. **Least privilege beats clever filters.** A read-only identity can't delete data, whatever the prompt says.
5. **Fail closed.** If a check can't decide (SQL won't parse), reject.
6. **Refuse politely, reveal nothing.** Give a short reason; no stack traces, prompts, keys or connection details.
7. **Test guards like code.** Unit tests (`tests/test_guards.py`), attack prompts in the evaluation set, red-teaming (Phase 4).
8. **Monitor.** Log and trace every block. A spike means someone is probing.

## Generic vs agent-specific

| Generic (shared library or gateway, every agent) | Agent-specific (next to the agent's tools) |
|---|---|
| Input length and empty checks | Topic scope ("only house questions") |
| Prompt injection and jailbreak detection (Prompt Shields) | Tool argument rules (sqft 500–5000) |
| Content safety on input and output | Data rules (SQL table allow-list, row caps) |
| PII detection and redaction | Business rules ("never quote a price without the model") |
| Secret-leak checks on output | The agent's identity and its permissions |
| Rate limits, timeouts, token and step budgets | |
| Logging and tracing of blocks | |

Where they live as the platform grows:
- **Phase 5 and 6:** the generic code moves to a shared package used by the supervisor and every specialist agent.
- **Phase 7:** gateway-level guards (rate limits, token quotas, content safety) move into APIM, applied centrally to all agents.

## Approach for a new agent

1. **Threat model.** What can the tools *do*: read, write, spend money, send email? What is the worst outcome?
2. **Give the agent's identity the smallest set of permissions** that still lets it work.
3. **Validate every tool argument with a schema.** Keep the dangerous tools (free-form SQL, HTTP, code) behind a dedicated validator.
4. **Add the generic input and output guards.**
5. **Write attack cases into the evaluation set.** Run them in CI.
6. **Trace and alert in production.**
