"""Evaluation runner: golden set -> /chat -> deterministic checks + LLM-as-judge -> scored report.

Run inside the agent image (it has httpx + langchain-openai), against local or Azure:
  docker run --rm -e FOUNDRY_API_KEY -v "$PWD/eval:/eval" house-agent \
      python /eval/run_eval.py --url http://host.docker.internal:8000/chat --label local
  ... --url https://acrhouseagents.<env>.uaenorth.azurecontainerapps.io/chat --label azure

Checks (all deterministic unless marked):
  contains          numbers that must appear (commas and $ are stripped before matching)
  contains_text     phrases that must appear (case-insensitive)
  not_contains_text phrases that must NOT appear (leaks, false claims)
  agent / agent2    supervisor must delegate to these specialists (routing accuracy)
  tool              the specialist must call this tool
  no_tool           no delegation at all (refusals, asking for missing info)
  table_total       a rendered table must report this total row count
  max_input_tokens  token budget for the whole question across all agents
  judge (LLM)       a rubric graded by a stronger model (gpt-5.4): PASS/FAIL with a reason
  groundedness (LLM, reported separately): every number in the answer is supported by the tool results
"""
import argparse
import json
import os
import re
import statistics
import time
import uuid

import httpx
from langchain_openai import ChatOpenAI

HERE = os.path.dirname(os.path.abspath(__file__))


def judge_llm():
    # A stronger model from the same Foundry resource. gpt-5.x reasoning models only accept the default
    # temperature, so it is not set here.
    return ChatOpenAI(base_url=os.getenv("FOUNDRY_BASE_URL", "https://ai103-agenticsystem-1.openai.azure.com/openai/v1"),
                      api_key=os.environ["FOUNDRY_API_KEY"], model=os.getenv("JUDGE_MODEL", "gpt-5.4"), timeout=120)


def ask_judge(llm, rubric, question, answer, tool_results):
    prompt = (
        "You are a strict evaluator of an AI assistant. Grade ONE criterion.\n"
        f"CRITERION: {rubric}\n\nUSER QUESTION: {question}\n\nTOOL RESULTS (ground truth the assistant saw):\n"
        f"{json.dumps(tool_results, default=str)[:6000]}\n\nASSISTANT ANSWER: {answer}\n\n"
        'Think about it, then reply with ONLY a JSON object: {"reason": "<one sentence>", "verdict": "PASS" or "FAIL"}')
    try:
        text = llm.invoke(prompt).content
    except Exception as e:
        # Azure OpenAI's built-in content filter can block the JUDGE's request when the graded text contains a
        # jailbreak. That says nothing about the agent, so fall back to the case's deterministic checks.
        if "content management policy" in str(e) or "content_filter" in str(e):
            return None, "judge request blocked by Azure content filter; graded on deterministic checks only"
        raise
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    return v.get("verdict") == "PASS", v.get("reason", text[:200])


def route(r):
    """Supervisor tools (agents) and nested specialist tools from the /chat response."""
    agents, tools, results = [], [], []
    for c in r.get("tool_calls", []):
        agents.append(c["tool"])
        res = c.get("result") if isinstance(c.get("result"), dict) else {}
        results.append(res)
        for s in res.get("tool_calls", []):
            tools.append(s["tool"])
            results.append(s.get("result"))
    return agents, tools, results


def digits(s):
    return re.sub(r"[,$\s]", "", s)


def check(case, r):
    exp, ans = case.get("expect", {}), r.get("answer", "")
    agents, tools, _ = route(r)
    fails = []
    for n in exp.get("contains", []):
        if n not in digits(ans):
            fails.append(f"missing number {n}")
    for t in exp.get("contains_text", []):
        if t.lower() not in ans.lower():
            fails.append(f"missing text '{t}'")
    for t in exp.get("not_contains_text", []):
        if t.lower() in ans.lower():
            fails.append(f"forbidden text '{t}'")
    for key in ("agent", "agent2"):
        if exp.get(key) and exp[key] not in agents:
            fails.append(f"not routed to {exp[key]} (got {agents})")
    if exp.get("tool") and exp["tool"] not in tools and exp["tool"] not in agents:
        fails.append(f"tool {exp['tool']} not called (got {tools})")
    if exp.get("no_tool") and agents:
        fails.append(f"expected no delegation, got {agents}")
    if "table_total" in exp:
        total = (r.get("tables") or [{}])[0].get("total")
        if total != exp["table_total"]:
            fails.append(f"table total {total} != {exp['table_total']}")
    if "max_input_tokens" in exp and r["tokens"]["input"] > exp["max_input_tokens"]:
        fails.append(f"input tokens {r['tokens']['input']} > {exp['max_input_tokens']}")
    return fails


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--label", default="run")
    p.add_argument("--no-judge", action="store_true")
    a = p.parse_args()
    cases = [json.loads(l) for l in open(os.path.join(HERE, "golden_set.jsonl")) if l.strip()]
    llm = None if a.no_judge else judge_llm()
    run_id, threads, rows = uuid.uuid4().hex[:8], {}, []

    for case in cases:
        thread = threads.setdefault(case["thread"], f"{run_id}-{case['thread']}") if case.get("thread") else None
        t0 = time.perf_counter()
        try:
            r = httpx.post(a.url, json={"message": case["question"], "thread_id": thread}, timeout=240).json()
        except Exception as e:  # network / 5xx: a failed case, not a crashed run
            r = {"answer": f"<request failed: {e}>", "tool_calls": [], "tokens": {"input": 0, "output": 0}}
        wall = round((time.perf_counter() - t0) * 1000)
        fails = check(case, r)
        agents, tools, results = route(r)
        judge_ok = judge_reason = grounded = None
        if llm and case.get("judge"):
            judge_ok, judge_reason = ask_judge(llm, case["judge"], case["question"], r["answer"], results)
            if judge_ok is False:
                fails.append(f"judge: {judge_reason}")
        if llm and tools and re.search(r"\d", r["answer"]):
            grounded, _ = ask_judge(llm, "Every number in the answer is supported by (or correctly derived from) the "
                                    "TOOL RESULTS. Rounding and formatting differences are fine.",
                                    case["question"], r["answer"], results)
        row = {"id": case["id"], "category": case["category"], "pass": not fails, "fails": fails,
               "agents": agents, "tools": tools, "latency_ms": r.get("latency_ms", wall),
               "tokens": r.get("tokens"), "grounded": grounded, "judge": judge_reason,
               "answer": r.get("answer", "")[:300]}
        rows.append(row)
        print(f"{'PASS' if row['pass'] else 'FAIL'} {case['id']:<4} {case['category']:<12} {row['latency_ms']:>6} ms "
              f"tok {row['tokens']['input']:>5}/{row['tokens']['output']:<4} {','.join(agents) or '-'} "
              f"{'; '.join(fails)[:150]}")

    # ---------------------------------------------------------------- summary
    by_cat = {}
    for row in rows:
        by_cat.setdefault(row["category"], []).append(row["pass"])
    routed = [r for r, c in zip(rows, cases) if c.get("expect", {}).get("agent")]
    routing_ok = sum(1 for r, c in zip(rows, cases) if c.get("expect", {}).get("agent")
                     and c["expect"]["agent"] in r["agents"])
    lat = sorted(r["latency_ms"] for r in rows)
    grounded = [r["grounded"] for r in rows if r["grounded"] is not None]
    summary = {
        "label": a.label, "url": a.url, "cases": len(rows), "passed": sum(r["pass"] for r in rows),
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in by_cat.items()},
        "routing_accuracy": f"{routing_ok}/{len(routed)}",
        "groundedness": f"{sum(grounded)}/{len(grounded)}" if grounded else "n/a",
        "judge_blocked": sum(1 for r in rows if r["judge"] and "blocked" in r["judge"]),
        "latency_ms": {"p50": statistics.median(lat), "p95": lat[int(0.95 * (len(lat) - 1))]},
        "avg_input_tokens": round(statistics.mean(r["tokens"]["input"] for r in rows)),
        "judge_model": None if a.no_judge else os.getenv("JUDGE_MODEL", "gpt-5.4"),
    }
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(HERE, "results", f"{stamp}-{a.label}.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2, default=str)
    print("\nSUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
