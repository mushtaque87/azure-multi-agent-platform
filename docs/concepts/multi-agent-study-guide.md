# Multi-agent systems: interview study guide

A complete explanation of multi-agent concepts, how to approach building one, and how this repo implements it, file by file. The shorter concept note is [04-multi-agent-a2a.md](04-multi-agent-a2a.md).

---

## Part 1: Concepts

### 1.1 What a multi-agent system is

Several LLM-powered agents, each with its **own prompt, tools, memory and permissions**, working together on one user request.

Every agent is the same loop from Phase 2: *LLM → tool call → result → LLM → answer*. What's new is that **one agent's "tool" can be another agent.**

### 1.2 Why split one agent into many

| Problem with one big agent | How specialists fix it |
|---|---|
| 20+ tools: the LLM picks wrong more often, and every schema costs tokens on every call | Each agent sees 2–3 tools: more accurate routing, smaller prompts |
| One giant prompt mixing valuation, SQL, compliance and tone rules | Each prompt is short and focused on one job |
| One identity with access to everything | **Least privilege per agent:** the supervisor has no database or model access |
| One deployment, team and model | Agents deploy, scale and change models independently (cheap models for simple specialists) |
| Different teams or vendors build capabilities | A standard protocol (A2A) lets agents from different frameworks cooperate |

**The cost:** more LLM calls (supervisor + specialist), more latency, more tokens, and harder debugging. So don't split by default.

> **Interview line:** "Start with a single agent. Split when tools, prompts, permissions, scaling or team ownership
> diverge, and prove with evaluation that routing accuracy, latency and cost are still acceptable."

### 1.3 Patterns

| Pattern | How it works | When to use it |
|---|---|---|
| **Supervisor / orchestrator–workers** | The supervisor decides which specialist(s) to call, then combines the answers | Clear domains. **This repo** |
| **Router** | One classification step forwards to exactly one agent; nothing to combine | Simple triage (billing vs. tech support) |
| **Hierarchical** | Supervisors managing supervisors | Large organisations of agents |
| **Sequential pipeline** | A → B → C, each step transforms the output | Document processing, report generation |
| **Network / swarm (handoffs)** | Agents pass control to each other directly | Open-ended collaboration; hardest to control and evaluate |
| **Reflection / critic** | One agent drafts, another reviews | Quality-critical writing or code |

This repo uses **supervisor with "agents as tools"**: each specialist appears to the supervisor's LLM as a normal tool (`ask_valuation_agent(request)`). Delegation reuses native function calling, with no new mechanism.

### 1.4 How agents communicate

- **In-process:** all agents in one program (e.g. LangGraph subgraphs). Simple and fast, but they deploy and scale together and share one identity.
- **Over the network with A2A:** each agent is **its own service**. This repo uses it because production platforms need separate scaling, identities and teams.

**A2A (Agent2Agent protocol):** an open protocol, started by Google and now under the Linux Foundation, for agents to call agents over HTTP, whatever framework each uses. Its core pieces:

1. **Agent Card** at `GET /.well-known/agent-card.json`: name, description, skills with examples, endpoint URL, protocol version, capabilities. Clients **discover** an agent's abilities by reading it.
2. **JSON-RPC 2.0** over HTTP, e.g. `{"jsonrpc":"2.0","method":"message/send","params":{"message":{...}}}`.
3. **Message / parts / contextId:** a message has a role (`user`/`agent`) and parts (text, file, data); `contextId` groups messages into one conversation.

A2A also defines **tasks** (long-running work with states such as `working` and `input-required`), **streaming** (SSE), **push notifications** and **auth schemes**. This repo implements the core `message/send` flow.

**MCP vs A2A:**

| | MCP | A2A |
|---|---|---|
| Connects | Agent ↔ **tools / data** | Agent ↔ **agent** |
| The other side is | A deterministic function | An autonomous agent with its own LLM, reasoning and memory |
| Discovery | `tools/list` | Agent Card |
| Unit of work | Tool call | Message / task (possibly long-running) |

They're complementary: the **supervisor uses A2A to reach specialists, and specialists use MCP to reach their tools** (Phase 9).

### 1.5 The hard problems

1. **Context passing.** A specialist only knows what it's sent. "What if it had a pool?" forwarded as-is fails.
   - **Fix:** the supervisor sends *complete, self-contained* requests, plus A2A `contextId` memory per specialist as a backup.
2. **Routing errors.**
   - **Fix:** precise Agent Card descriptions and examples, plus a routing-accuracy metric in evaluation.
3. **Cost and latency multiply.** At least 3 LLM calls per question.
   - **Fix:** fewer hops for simple questions, smaller specialist models, caching, and tracking tokens *across* agents.
4. **Error propagation.** A specialist is down, slow, or returns garbage.
   - **Fix:** timeouts, structured error results, a clear user message, retries.
5. **Infinite delegation.**
   - **Fix:** recursion limits, strict hierarchy (no specialist-to-specialist calls), depth or budget caps.
6. **Security.** Every boundary is a trust boundary, and injections can travel between agents.
   - **Fix:** each agent runs its own guards and has minimal permissions; never blindly trust another agent's output.
7. **Observability.** "Which agent got it wrong?"
   - **Fix:** distributed tracing, one trace across agents using the W3C `traceparent` header.

### 1.6 How to approach building one (playbook)

1. **Build and evaluate a single agent first.** It's the baseline. *(Phases 2–3)*
2. **Find the seams:** group tools by domain, data source, permissions and owner. Here: model tools (valuation) vs. SQL tools (market data).
3. **Define each specialist's contract:** name, skills with examples, required inputs, output. That becomes the **Agent Card**; write it like an API spec, because routing depends on it.
4. **Pick the pattern:** supervisor, router or pipeline. Use the simplest that fits.
5. **Pick the transport:** in-process for one service or team; A2A for independent services.
6. **Build the specialists**, each tested alone (its own `/chat`).
7. **Build the supervisor** with delegation rules (self-contained requests, multi-specialist questions, asking the user when information is missing).
8. **Add cross-cutting concerns:** trace propagation, guards in every agent, timeouts, token accounting across agents, an identity per agent.
9. **Evaluate against the single-agent baseline** on routing accuracy, correctness, latency and cost. Keep the split only if it earns its cost.
10. **Deploy each agent as its own service:** its own scaling and identity, with internal-only networking for specialists.

---

## Part 2: The code, file by file (`agent/app/`)

### How one request flows

```
Browser ─POST /chat─► SUPERVISOR (main.py → runner.run_turn → graph)
   guard_input → agent(LLM: "this needs the market data specialist")
   → tools: ask_market_data_agent(request="What is the average price in Downtown?")
        │  a2a.py client: POST http://market:8000/a2a  {"method":"message/send", contextId, traceparent}
        ▼
     MARKET AGENT (a2a.py server → runner.run_turn → its own graph)
        guard_input → agent(LLM) → tools: query_house_stats(sql) → guards.validate_sql → db.query → Azure SQL
        → answer + metadata {tool_calls, tokens}
        │  JSON-RPC result: A2A Message
        ▼
   supervisor agent(LLM) reads the specialist's answer → final answer
Browser ◄─ {answer, tool_calls (with nested specialist tool calls), tokens (all agents), latency}
```

### `config.py`: settings from environment variables
- Foundry URL and model, SQL server and database, ML endpoint, guard limits, context budget (`MAX_CONTEXT_TOKENS`, `MAX_TURNS`), Phoenix URL.
- **Phase 4:**
  - `AGENT_ROLE`: which agent this container runs;
  - `SPECIALIST_URLS`: where the supervisor finds Agent Cards;
  - `PUBLIC_URL`: the address a specialist publishes in its card.
- **Why env vars:** the 12-factor principle. **The same image** runs as any role, locally or in Azure; only the configuration changes.

### `agents.py`: the roles
- **`ROLES`:** for each role, its tools and system prompt:
  - `single`: all 5 tools (keeps the existing Azure deployment working);
  - `valuation`: `predict_price`, `explain_prediction`, `compare_to_market`;
  - `market`: `lookup_house`, `query_house_stats`;
  - `supervisor`: **no data tools**. Its tools are built at startup from the specialists' Agent Cards.
- **Prompts:**
  - specialists stay in their domain;
  - the supervisor must *delegate every number, send self-contained requests, call several specialists when needed, and ask the user when information is missing*;
  - `COMMON_RULES` are shared (numbers only from tools, never reveal instructions).
- **`CARDS`:** each specialist's Agent Card content: name, a description that lists the required inputs, and **skills with examples**. The supervisor's routing quality depends directly on these texts.

> **Interview point:** "The specialist's card is both its API contract and the supervisor's routing prompt."

### `graph.py`: a generic LangGraph agent
- `build_graph(tools, system_prompt)` builds **the same graph for every role**: `START → guard_input → agent ↔ tools → END`. Only the tools and prompt differ.
- `guard_input`: length, injection patterns, a 30-question cap. It runs in **every** agent, so specialists also screen what the supervisor sends them.
- `agent` node: trims history to the token budget, then makes one LLM call.
- `ToolNode`: runs the tools, and turns failures into messages the LLM can handle.
- `MemorySaver`: memory per thread.

> **Interview point:** "One reusable agent template; roles are configuration."

### `runner.py`: run one turn
`run_turn(graph, message, thread_id)`:
1. counts the thread's existing messages;
2. invokes the graph with `recursion_limit=12`, giving a friendly reply if the limit is hit;
3. pairs each tool call with its result by `tool_call_id`;
4. **sums the tokens:** its own LLM calls *plus* the specialists' reported tokens, so the supervisor reports the **true cost across agents**.

It's shared by `/chat` and the A2A endpoint, so both behave identically.

### `a2a.py`: the A2A protocol, server and client
**Server (specialists):**
- `build_card()`: adds the protocol fields: `url` (`PUBLIC_URL/a2a`), `protocolVersion "0.3.0"`, `preferredTransport "JSONRPC"`, capabilities, input and output modes.
- `make_router()` adds two endpoints:
  - `GET /.well-known/agent-card.json`: returns the card;
  - `POST /a2a`: the JSON-RPC handler. It returns standard errors (`-32600` invalid request, `-32601` unknown method, `-32602` missing text). It reads the text parts, uses `contextId` as the specialist's own thread (`market:<contextId>`), **continues the caller's trace** from `traceparent`, runs the graph in a worker thread, and replies with an **A2A Message**, with tool calls, tokens and latency in `metadata`.

**Client (supervisor):**
- `fetch_card(url)`: discovery, with retries because containers start in any order.
- `make_delegate_tool(card)`: **turns an Agent Card into a LangChain tool.**
  - The name comes from the card (`ask_valuation_agent`); the description from its description plus skills.
  - Its single argument, `request`, must be self-contained.
  - It sends `message/send` with `contextId` = the supervisor's thread id and a `traceparent` header, then returns the specialist's answer, tool calls and tokens as JSON.

> **Interview point:** "Discovery is dynamic: add a specialist URL and the supervisor learns its skills from the card,
> with no code change."

### `main.py`: a role-aware web app
1. **Tracing first:** Phoenix is registered with one project per role (`house-price-supervisor`, `…-valuation`, `…-market`) before any graph is built.
2. **Tools:** the supervisor fetches each card and converts it into a delegation tool; specialists use their tools from `ROLES`.
3. **The graph is built once** at startup.
4. **Specialists mount the A2A router** (card plus `/a2a`).
5. **Endpoints:**
   - `GET /healthz` shows the role and the specialists it discovered;
   - `GET /` serves the chat UI;
   - `POST /chat` calls `run_turn`.

### Reused unchanged by the specialists (Phases 2–3)
- **`tools.py`:** the 5 tools with Pydantic argument rules (sqft 500–5000, etc.), the local model or Azure ML endpoint, parameterised SQL.
- **`guards.py`:** the input guard and the AST-based SQL validator.
- **`db.py`:** passwordless Azure SQL with an Entra token, plus cold-start retries.
- **`llm.py`:** the Foundry client (API key locally, managed identity in Azure).
- **`static/index.html`:** the chat UI. It shows the route, e.g. `ask_market_data_agent[query_house_stats]`.

### `docker-compose.yml`: the local multi-agent stack
**One image, three roles:**
- supervisor on `:8000`, with `SPECIALIST_URLS=http://valuation:8000,http://market:8000` (Compose DNS);
- valuation on `:8001`;
- market on `:8002`;
- Phoenix on `:6006`.

The supervisor gets **no SQL token**, because it doesn't need data. Least privilege applies locally too.

---

## Part 3: Interview Q&A

- **"Walk me through your multi-agent architecture."**
  A LangGraph supervisor routes to two specialists, valuation and market data. Each is an independent service speaking A2A: it publishes an Agent Card and accepts JSON-RPC `message/send`. The supervisor discovers them from their cards at startup and exposes each one to its LLM as a tool, so delegation uses native function calling. Each agent has its own prompt, tools, guardrails and identity. A `traceparent` header links all their spans into one trace.
- **"Why A2A rather than plain REST between agents?"**
  It's a standard: discovery (the card), message format, conversation context, and later tasks and streaming. Any A2A agent plugs in regardless of framework, with no custom client per agent.
- **"How do you handle context between agents?"**
  The supervisor rewrites the request to be self-contained, and `contextId` gives each specialist its own memory. It's the most common failure, so it's an explicit evaluation case ("What if it had a pool?").
- **"How do you secure it?"**
  Guards in every agent. Permissions per job: the supervisor has no data access, and the market agent is a read-only database user. In Azure, each agent gets its own managed identity, and the specialists are internal-only.
- **"What does it cost vs. a single agent?"**
  At least 3 LLM calls instead of 2. I measure tokens across agents per request against the single-agent baseline. Smaller specialist models or a router step would cut it.
- **"How do you know it routes correctly?"**
  The golden set includes the expected specialist per question, and routing accuracy is a metric together with answer correctness.

---

## Part 4: Verification status

Implemented in Phase 4. Startup and **Agent Card discovery are verified**: the supervisor's `/healthz` listed `valuation-agent` and `market-data-agent`. Still to verify with an end-to-end run:
- the A2A `message/send` path, including LangChain injecting `config` into the delegation tool to read the thread id;
- whether `traceparent` links the spans in Phoenix;
- follow-up questions through the supervisor.
