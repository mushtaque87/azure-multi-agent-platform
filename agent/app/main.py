"""HTTP API for the agent: POST /chat (JSON), GET /healthz."""
import json   # parse tool results (tools return JSON strings)
import time   # measure latency
import uuid   # generate a conversation id when the client doesn't send one

from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
# Pydantic: declare the request shape; FastAPI validates every request against it (HTTP 422 if invalid)
from pydantic import BaseModel, Field

from .graph import build_graph

app = FastAPI(title="House Price Agent", version="1.0")  # the web app; uvicorn serves it on port 8000
# Built ONCE at server start, shared by all requests: creating the LLM client and compiling the
# graph has a cost, and the in-memory conversation store must live as long as the server.
graph = build_graph()


class ChatRequest(BaseModel):
    """Request body for POST /chat."""
    message: str = Field(max_length=4000)  # first, simplest guardrail: oversized bodies rejected here
    thread_id: str | None = None           # same thread_id = same conversation memory; None = new chat


@app.get("/healthz")
def healthz():
    """Liveness probe: Container Apps calls this to know the container is up."""
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest):
    # Continue the client's conversation, or start a new one with a random id (returned to the client)
    thread_id = req.thread_id or str(uuid.uuid4())
    start = time.perf_counter()  # high-resolution timer start

    # The config tells the checkpointer which saved conversation to load/update
    cfg = {"configurable": {"thread_id": thread_id}}
    # How many messages this thread had BEFORE this turn (0 for a new thread). The result contains
    # the whole history, so we use this count to slice out only this turn's messages below.
    before = len(graph.get_state(cfg).values.get("messages", []))

    # Run the graph to END. Input = the new user message (appended to the saved history).
    # recursion_limit = max node steps for this run, so a tool-calling loop can't run (and bill) forever.
    result = graph.invoke({"messages": [HumanMessage(req.message)]}, {**cfg, "recursion_limit": 12})
    new = result["messages"][before:]  # this turn only: user msg, AI tool calls, tool results, answer

    # Pair each tool call with its result using the tool_call_id the LLM assigned (works even if the
    # same tool is called twice in one turn)
    tool_calls, by_id = [], {}
    for m in new:
        if isinstance(m, AIMessage):
            for c in m.tool_calls:  # what the LLM asked to run, with which arguments
                by_id[c["id"]] = {"tool": c["name"], "args": c["args"]}
                tool_calls.append(by_id[c["id"]])
        elif isinstance(m, ToolMessage) and m.tool_call_id in by_id:
            try:
                by_id[m.tool_call_id]["result"] = json.loads(m.content)  # tools return JSON
            except (TypeError, ValueError):
                by_id[m.tool_call_id]["result"] = m.content  # e.g. a plain-text tool error

    # Token usage as reported by Foundry for each LLM call this turn (for cost tracking)
    usage = [m.usage_metadata for m in new if isinstance(m, AIMessage) and m.usage_metadata]
    return {
        "thread_id": thread_id,                 # send this back next time to continue the conversation
        "answer": new[-1].content,              # the last message is the final answer (or the refusal)
        "tool_calls": tool_calls,               # transparency: which tools ran, with what, returning what
        "latency_ms": round((time.perf_counter() - start) * 1000),
        "tokens": {"input": sum(u["input_tokens"] for u in usage), "output": sum(u["output_tokens"] for u in usage)},
    }
