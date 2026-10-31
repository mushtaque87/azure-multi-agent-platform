"""HTTP API for every agent role.
  all roles:     GET /healthz, POST /chat (JSON), GET / (chat UI)
  specialists:   GET /.well-known/agent-card.json, POST /a2a (A2A JSON-RPC)
The role comes from AGENT_ROLE (single | supervisor | valuation | market)."""
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import FileResponse
# Pydantic: declare the request shape; FastAPI validates every request against it (HTTP 422 if invalid)
from pydantic import BaseModel, Field

from . import config
from .agents import CARDS, ROLES

# Tracing must be set up BEFORE the graph is built, so every LLM call, tool call and graph step
# is recorded as an OpenTelemetry span and sent to Phoenix. One Phoenix project per agent role.
if config.PHOENIX_COLLECTOR_ENDPOINT:
    from phoenix.otel import register
    register(project_name=f"house-price-{config.AGENT_ROLE}",
             endpoint=f"{config.PHOENIX_COLLECTOR_ENDPOINT}/v1/traces",
             auto_instrument=True)  # turns on the OpenInference LangChain instrumentor

from .a2a import fetch_card, make_delegate_tool, make_router  # noqa: E402 (after tracing setup)
from .graph import build_graph  # noqa: E402
from .runner import run_turn  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
role = ROLES[config.AGENT_ROLE]

if config.AGENT_ROLE == "supervisor":
    # A2A discovery: read each specialist's Agent Card and turn it into a delegation tool
    cards = [fetch_card(url) for url in config.SPECIALIST_URLS]
    tools = [make_delegate_tool(card) for card in cards]
else:
    cards, tools = [], role["tools"]

app = FastAPI(title=f"House Price Agent ({config.AGENT_ROLE})", version="2.0")
# Built ONCE at server start, shared by all requests: creating the LLM client and compiling the
# graph has a cost, and the in-memory conversation store must live as long as the server.
graph = build_graph(tools, role["prompt"])

if config.AGENT_ROLE in CARDS:
    # Specialists speak A2A: publish an Agent Card and accept message/send
    app.include_router(make_router(graph, CARDS[config.AGENT_ROLE], config.AGENT_ROLE))


class ChatRequest(BaseModel):
    """Request body for POST /chat."""
    message: str = Field(max_length=4000)  # first, simplest guardrail: oversized bodies rejected here
    thread_id: str | None = None           # same thread_id = same conversation memory; None = new chat


@app.get("/healthz")
def healthz():
    """Liveness probe: Container Apps calls this to know the container is up."""
    return {"status": "ok", "role": config.AGENT_ROLE,
            "specialists": [c["name"] for c in cards]}


@app.get("/")
def index():
    """The single-page chat UI (same origin as /chat, so no CORS setup is needed)."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/chat")
def chat(req: ChatRequest):
    # Continue the client's conversation, or start a new one with a random id (returned to the client)
    return run_turn(graph, req.message, req.thread_id or str(uuid.uuid4()))
