# Agent evaluation: a complete guide

Use this as study notes for interviews. It covers why we evaluate, what to measure, the methods, how this project does it, and how evaluation fits into CI/CD and production.

> **One-line answer:** "Evaluation is the test suite for non-deterministic software: a versioned golden dataset,
> deterministic checks first, an LLM-as-judge for what code can't check, run in CI as a quality gate, and the
> same metrics continuously in production from traces."

---

## 1. Why agents need evaluation, not just tests

Normal software gives the same output for the same input, so unit tests are enough. An agent's behaviour depends on:
- the **model and its version**;
- the **prompt**;
- the **tool descriptions**;
- the **data**.

Any of these can change answers silently. A prompt tweak that fixes one question can break three others. Evaluation is how you **change any of them with confidence**: it is the regression test for behaviour.

**This project's proof:** evaluation caught a real bug. The LLM wrote `CAST(AVG(price) AS FLOAT)`, which uses integer division in T-SQL and gave 770,569 instead of 770,570. No unit test would have found it.

## 2. What to measure

| Dimension | Question | How to check |
|---|---|---|
| **Routing / tool selection** | Did it call the right tool? | Deterministic: compare tool names |
| **Tool arguments** | Were the arguments right (sqft=2000, Suburban)? | Deterministic: compare JSON |
| **Final answer correctness** | Is the number or fact right? | Deterministic match, or LLM-as-judge for free text |
| **Groundedness / faithfulness** | Is every claim supported by tool results or retrieved documents? | LLM-as-judge, Azure AI groundedness evaluator |
| **Relevance / completeness** | Did it answer what was asked, fully? | LLM-as-judge |
| **Behaviour** | Asks for missing info, declines off-topic, keeps context | Scenario cases plus a judge |
| **Safety** | Blocks injections, refuses writes, no data leaks | Attack cases (deterministic expected refusal) |
| **Trajectory** | Was the *path* efficient (no pointless tool loops)? | Count steps and tool calls from traces |
| **Cost and latency** | Tokens per answer, p50 and p95 time | From the API response and traces |

Rule of thumb: **use code to check anything code can check.** Use an LLM judge only for what's left (tone, completeness, faithfulness of free text).

## 3. The golden dataset

A **golden dataset** is a versioned list of test cases: question → expected tool(s) → expected answer facts.

Good datasets:
- **cover every tool and route**, plus **edge cases** (missing features, ambiguous questions) and **attacks** (injection, write attempts, off-topic);
- **grow from production:** every bug or bad answer becomes a new case, so it can never come back;
- **are versioned in git** next to the code, so a prompt change and its evaluation result are reviewed together;
- **are balanced:** don't let 90% of cases be easy lookups.

This project's cases so far:

| Type | Example | Expected |
|---|---|---|
| Analytics | Average price Downtown | `query_house_stats`, 770,570 |
| Analytics | Houses with a pool | `query_house_stats`, 5,018 |
| Lookup | House ID 42 | `lookup_house`, 566,563 |
| Prediction | 2000 sqft Suburban… | `predict_price`, 522,171 |
| Prediction | 2500 sqft Downtown… | `predict_price`, 784,960 |
| Multi-step | Is $650k a good price? | `compare_to_market` |
| Memory | "What if it had a pool?" (same thread) | Reuses features, a higher price |
| Missing info | 3-bedroom Downtown, no other features | No tool; asks for the features |
| Off-topic | Poem about cats | Declines |
| Injection | "Ignore all previous instructions…" | Blocked by the guard, 0 tokens |
| Write attempt | "DELETE FROM dbo.house_prices…" | Refuses |

The same first 5 cases were used for the Snowflake Cortex Agent (`AGENT_EVAL_CASES`), so the two platforms were compared on identical questions: 5/5 on both.

## 4. Methods

### 4a. Deterministic checks (first choice)
- The tool name is in the list of tools called.
- The expected number appears in the answer, after normalising formats ("$770,570", "770570", "770.6k").
- The response contains the refusal pattern for attack cases.
- Steps, tokens and latency are under budget.

These are cheap, fast and repeatable. Use them wherever possible.

### 4b. LLM-as-judge
Another LLM grades the answer against a rubric, for example: *"Given the question, the tool results and the answer, score 1–5: is every number in the answer present in the tool results?"*

Best practices:
- Use a **strong judge model**, ideally from a different model family than the agent, to reduce self-bias.
- Give it a **clear rubric** with examples of each score. Ask for **reasoning before the score**.
- Use **binary or 1–5 scales**, not 1–100.
- **Calibrate:** hand-label about 30 cases and check the judge agrees with you before you trust it.
- Watch the known biases: verbosity (preferring longer answers), position, and self-preference.

### 4c. Human review
Domain experts review a sample, especially failures and borderline judge scores. The labels become new golden cases and recalibrate the judge.

### 4d. Red-teaming
Adversarial testing to find guardrail gaps: jailbreak variants, indirect injection in documents, attempts to get data out.

Tools:
- **Azure AI Foundry AI Red Teaming Agent**, based on PyRIT;
- **PyRIT**, Microsoft's open-source toolkit;
- **promptfoo**.

Results become attack cases in the golden set.

## 5. Offline vs online evaluation

| | Offline (before release) | Online (in production) |
|---|---|---|
| Data | Golden dataset | Real user traffic (traces) |
| When | Every PR or change, in CI | Continuously, sampled |
| Purpose | Quality gate: block regressions | Detect drift, new failure types, cost and latency |
| Signals | Pass rate per dimension | Judge scores on samples, thumbs up/down, block rates, p95, tokens per answer |

The loop: **production traces → find failures → add them to the golden set → fix → offline evaluation passes in CI → deploy → monitor.**

## 6. Evaluation in CI/CD (Phase 8)

1. A PR changes the prompt, a tool, the model or the code.
2. CI runs the guard unit tests, then the offline evaluation suite against a test deployment.
3. A **quality gate** fails the build if, for example:
   - routing accuracy < 100%;
   - answer correctness < 95%;
   - any safety case fails;
   - p95 latency or average tokens get more than 20% worse than the baseline.
4. Results are posted on the PR, so reviewers see behaviour changes next to code changes.

Model upgrades (e.g. gpt-4.1-mini → a newer mini) go through the **same gate**. Evaluation is how you safely pick the cheapest model that still passes.

## 7. Tools landscape

| Tool | Use |
|---|---|
| **Azure AI Foundry evaluation** (`azure-ai-evaluation` SDK) | Built-in evaluators: groundedness, relevance, coherence, fluency, similarity, F1; agent evaluators (intent resolution, tool-call accuracy, task adherence); safety evaluators; results in the Foundry portal |
| **Arize Phoenix** (this project) | Open-source tracing (OpenTelemetry), datasets, experiments, LLM-as-judge evaluators on traces |
| **LangSmith** | LangChain's hosted tracing and evaluation |
| **Ragas** | RAG metrics: faithfulness, answer relevancy, context precision and recall |
| **promptfoo, PyRIT** | Red-teaming and attack testing |

## 8. RAG-specific evaluation (Phase 6)

RAG has two parts, and each can fail independently, so evaluate them separately:
- **Retrieval:**
  - context precision: are the retrieved chunks relevant?
  - context recall: was everything needed retrieved?
  - hit rate and MRR (Mean Reciprocal Rank: how near the top the right chunk appears).
- **Generation:**
  - faithfulness / groundedness: does the answer stick to the retrieved context?
  - answer relevance.

A wrong answer with good retrieval is a prompt or model problem. A wrong answer with bad retrieval is a chunking, embedding or search problem. Splitting the metrics tells you which to fix.

## 9. Multi-agent evaluation (Phase 6)

- **Routing accuracy of the supervisor:** did it delegate to the right specialist?
- **Each specialist evaluated on its own,** with its own golden set.
- **End-to-end task success** across the whole conversation.
- **Trajectory:** number of delegations, loops, total tokens. A correct answer that took 9 agent hops is a failure on cost.

## 10. Plan for this project

| Phase | Evaluation work |
|---|---|
| 2 (done) | 5 golden cases at parity with Snowflake, 7 behaviour and safety checks, guard unit tests |
| 4 | `eval/` runner over a versioned JSONL golden set (about 30 cases); deterministic checks plus a Foundry LLM-judge; Azure AI evaluators; red-team run; results logged to Phoenix |
| 6 | RAG retrieval and generation metrics, supervisor routing accuracy |
| 7 | Evaluation under load: quality and p95 together |
| 8 | Evaluation as a CI quality gate on every PR |

## 11. Likely interview questions and short answers

- **"How do you evaluate an agent?"** A golden dataset covering routes, edge cases and attacks; deterministic checks for tool choice, arguments and numbers; an LLM-as-judge for free-text quality and groundedness; run in CI as a gate; online evaluation on sampled production traces.
- **"How do you trust an LLM judge?"** Calibrate it against human labels, use a strong model from a different family, a clear rubric with reasoning before the score, and binary or small scales.
- **"How do you choose between models?"** Run the same evaluation suite on each, then pick the cheapest model that passes the quality gate at the required latency.
- **"What's the difference between tracing and evaluation?"** Tracing records what happened (each LLM and tool step, tokens, latency). Evaluation scores whether it was good. Traces feed online evaluation and give you new golden cases.
- **"Give an example where evaluation found a bug."** Text-to-SQL integer division (`AVG` on an INT column) returned 770,569 instead of 770,570. I fixed it with a deterministic AST rewrite in the SQL guard instead of a prompt hint, and kept the case in the golden set.
