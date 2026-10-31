"""Run one conversational turn on any agent graph and summarise it (used by /chat and by A2A)."""
import json
import time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError  # raised when recursion_limit is hit


def run_turn(graph, message: str, thread_id: str) -> dict:
    start = time.perf_counter()  # high-resolution timer start
    # The config tells the checkpointer which saved conversation to load/update
    cfg = {"configurable": {"thread_id": thread_id}}
    # How many messages this thread had BEFORE this turn (0 for a new thread). The result contains
    # the whole history, so we use this count to slice out only this turn's messages below.
    before = len(graph.get_state(cfg).values.get("messages", []))

    # Run the graph to END. Input = the new user message (appended to the saved history).
    # recursion_limit = max node steps for this run, so a tool-calling loop can't run (and bill) forever.
    try:
        result = graph.invoke({"messages": [HumanMessage(message)]}, {**cfg, "recursion_limit": 12})
    except GraphRecursionError:
        # Friendly reply instead of HTTP 500 when the agent needs too many steps
        return {"thread_id": thread_id, "answer": "I couldn't complete that in a reasonable number of steps. "
                "Please ask a narrower question.", "tool_calls": [],
                "latency_ms": round((time.perf_counter() - start) * 1000), "tokens": {"input": 0, "output": 0}}
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

    # Token usage as reported by Foundry for each LLM call this turn (for cost tracking).
    # A supervisor's delegation results carry the specialist's tokens too - add them, so the total
    # reflects the true cost of the question across all agents.
    usage = [m.usage_metadata for m in new if isinstance(m, AIMessage) and m.usage_metadata]
    tokens = {"input": sum(u["input_tokens"] for u in usage), "output": sum(u["output_tokens"] for u in usage)}
    for c in tool_calls:
        sub = c.get("result", {}).get("tokens") if isinstance(c.get("result"), dict) else None
        if sub:
            tokens["input"] += sub.get("input", 0)
            tokens["output"] += sub.get("output", 0)
    return {
        "thread_id": thread_id,                 # send this back next time to continue the conversation
        "answer": new[-1].content,              # the last message is the final answer (or the refusal)
        "tool_calls": tool_calls,               # transparency: which tools ran, with what, returning what
        "latency_ms": round((time.perf_counter() - start) * 1000),
        "tokens": tokens,
    }
