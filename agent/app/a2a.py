"""Minimal A2A (Agent2Agent protocol v0.3) - server and client, written out so every step is visible.

Server (specialists):
  GET  /.well-known/agent-card.json   -> the Agent Card: who I am, what skills I have, where to call me
  POST /a2a   JSON-RPC 2.0, method "message/send" -> run my LangGraph agent, reply with an A2A Message
Client (supervisor):
  fetch_card(url)          -> read a specialist's Agent Card
  make_delegate_tool(card) -> wrap the specialist as a LangChain tool the supervisor's LLM can call

Implemented: Agent Card, message/send, contextId (conversation), text parts, metadata.
Not implemented (see docs/concepts/04-multi-agent-a2a.md): tasks, streaming, push notifications, auth schemes.
"""
import json
import re
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from . import config
from .runner import run_turn

PROTOCOL_VERSION = "0.3.0"
CARD_PATH = "/.well-known/agent-card.json"

# W3C trace-context propagation (traceparent header) so one question = one trace across agents.
# Optional: only active when OpenTelemetry is installed and tracing is configured.
try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.propagate import extract, inject
except ImportError:  # pragma: no cover
    otel_context = otel_trace = extract = inject = None
try:
    # OpenInference records LangChain spans via callbacks and does NOT make them the OpenTelemetry
    # "current" span, so plain inject() would find nothing. This helper returns the active tool span.
    from openinference.instrumentation.langchain import get_current_span as oi_current_span
except ImportError:  # pragma: no cover
    oi_current_span = None


# ----------------------------------------------------------------------------------------- server
def build_card(card: dict) -> dict:
    """Fill in the protocol fields around a role's name/description/skills (agents.CARDS)."""
    return {
        **card,
        "url": f"{config.PUBLIC_URL}/a2a",           # where clients send JSON-RPC
        "version": "1.0.0",                          # version of THIS agent
        "protocolVersion": PROTOCOL_VERSION,         # version of the A2A spec it speaks
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
    }


def rpc_error(rpc_id, code, message):
    """JSON-RPC 2.0 error object (codes from the JSON-RPC spec)."""
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def make_router(graph, card: dict, role: str) -> APIRouter:
    router = APIRouter()
    full_card = build_card(card)

    @router.get(CARD_PATH)
    def agent_card():
        return full_card

    @router.post("/a2a")
    async def a2a(request: Request):
        body = await request.json()
        rpc_id = body.get("id")
        if body.get("jsonrpc") != "2.0":
            return rpc_error(rpc_id, -32600, "Invalid Request: jsonrpc must be '2.0'")
        if body.get("method") != "message/send":
            return rpc_error(rpc_id, -32601, f"Method not found: {body.get('method')}")
        msg = (body.get("params") or {}).get("message") or {}
        text = "\n".join(p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text").strip()
        if not text:
            return rpc_error(rpc_id, -32602, "Invalid params: message needs at least one text part")
        # contextId groups messages into one conversation -> this agent's own thread memory
        context_id = msg.get("contextId") or str(uuid.uuid4())

        # Continue the caller's trace (supervisor -> this specialist) if a traceparent header was sent
        token = otel_context.attach(extract(dict(request.headers))) if otel_context else None
        try:
            # run_turn is synchronous (LangGraph invoke); run it in a worker thread, not the event loop
            from starlette.concurrency import run_in_threadpool
            turn = await run_in_threadpool(run_turn, graph, text, f"{role}:{context_id}")
        finally:
            if token is not None:
                otel_context.detach(token)

        # A2A Message reply. Our extension: tool calls and tokens in metadata, so the caller can show
        # the full route and add up the cost of the question across agents.
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {
            "kind": "message", "role": "agent", "messageId": str(uuid.uuid4()), "contextId": context_id,
            "parts": [{"kind": "text", "text": turn["answer"]}],
            "metadata": {"agent": full_card["name"], "tool_calls": turn["tool_calls"],
                         "tokens": turn["tokens"], "latency_ms": turn["latency_ms"]},
        }}

    return router


# ----------------------------------------------------------------------------------------- client
def fetch_card(base_url: str, attempts: int = 20, wait: float = 3) -> dict:
    """Discover a specialist: GET its Agent Card. Retries because containers start in any order."""
    for i in range(attempts):
        try:
            r = httpx.get(base_url.rstrip("/") + CARD_PATH, timeout=10)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


class DelegateInput(BaseModel):
    request: str = Field(description="A COMPLETE, self-contained request for the specialist, including every "
                                     "relevant detail from the conversation (e.g. all house features).")


def make_delegate_tool(card: dict) -> StructuredTool:
    """Turn an Agent Card into a tool: name from the card, description from its description + skills.
    The supervisor's LLM then chooses specialists exactly the way a single agent chooses tools."""
    name = "ask_" + re.sub(r"[^a-z0-9]+", "_", card["name"].lower()).strip("_")  # ask_valuation_agent
    skills = "; ".join(f"{s['name']}: {s['description']}" for s in card.get("skills", []))
    description = f"{card['description']} Skills - {skills}"
    url = card["url"]

    def call(request: str, config: RunnableConfig) -> str:
        # The supervisor's thread id becomes the A2A contextId, so the specialist keeps per-conversation memory
        thread_id = (config.get("configurable") or {}).get("thread_id", str(uuid.uuid4()))
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": {
            "message": {"role": "user", "messageId": str(uuid.uuid4()), "contextId": thread_id,
                        "parts": [{"kind": "text", "text": request}]}}}
        headers = {}
        if inject:
            span = oi_current_span() if oi_current_span else None
            # Put the tool's span into a context and serialise it as a W3C traceparent header, so the
            # specialist's spans join THIS trace (one question = one trace across agents)
            ctx = otel_trace.set_span_in_context(span) if span is not None else None
            inject(headers, context=ctx)
        r = httpx.post(url, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            return json.dumps({"agent": card["name"], "error": body["error"]["message"]})
        res = body["result"]
        answer = " ".join(p.get("text", "") for p in res.get("parts", []) if p.get("kind") == "text")
        meta = res.get("metadata", {})
        return json.dumps({"agent": card["name"], "answer": answer, "tool_calls": meta.get("tool_calls", []),
                           "tokens": meta.get("tokens", {}), "latency_ms": meta.get("latency_ms")}, default=str)

    return StructuredTool.from_function(func=call, name=name, description=description, args_schema=DelegateInput)
