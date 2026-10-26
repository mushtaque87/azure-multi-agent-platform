"""HTTP API for the agent: POST /chat (JSON), GET /healthz."""
import json
import time
import uuid

from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from .graph import build_graph

app = FastAPI(title="House Price Agent", version="1.0")
graph = build_graph()


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)
    thread_id: str | None = None  # same thread_id = same conversation memory


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest):
    thread_id = req.thread_id or str(uuid.uuid4())
    start = time.perf_counter()
    before = len(graph.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", []))
    result = graph.invoke({"messages": [HumanMessage(req.message)]},
                          {"configurable": {"thread_id": thread_id}, "recursion_limit": 12})
    new = result["messages"][before:]  # only this turn's messages

    tool_calls, by_id = [], {}
    for m in new:
        if isinstance(m, AIMessage):
            for c in m.tool_calls:
                by_id[c["id"]] = {"tool": c["name"], "args": c["args"]}
                tool_calls.append(by_id[c["id"]])
        elif isinstance(m, ToolMessage) and m.tool_call_id in by_id:
            try:
                by_id[m.tool_call_id]["result"] = json.loads(m.content)
            except (TypeError, ValueError):
                by_id[m.tool_call_id]["result"] = m.content
    usage = [m.usage_metadata for m in new if isinstance(m, AIMessage) and m.usage_metadata]
    return {
        "thread_id": thread_id,
        "answer": new[-1].content,
        "tool_calls": tool_calls,
        "latency_ms": round((time.perf_counter() - start) * 1000),
        "tokens": {"input": sum(u["input_tokens"] for u in usage), "output": sum(u["output_tokens"] for u in usage)},
    }
