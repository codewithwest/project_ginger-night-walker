# Goal-Aware Strands + Qwen 0.6B + Coding Model MCP Agent

> **Integration status:** This directory preserves the experimental standalone harness for reference and offline experiments. Night Walker now integrates the evidence-gated goal-state pattern in `nightwalker/goal_state.py` and an optional Strands source-selection adapter in `nightwalker/ollama_client.py`. The standalone runner's direct Playwright MCP executor and coding-model argument generator are deliberately not used by the core runtime; all production browser execution remains behind Night Walker's BrowserRuntime and PolicyEngine.

An **experimental**, goal-conditioned local browser harness. Three specialized models:

1. **Qwen3 0.6B** proposes success criteria at initialization and interprets actual results after each tool call. Python persistently owns the immutable user goal, evidence, and state.
2. **Strands Decider 2B** selects ONE semantic action from a fixed classification set.
3. **Small coding model** (example: Qwen2.5-Coder 1.5B) receives the original goal, remaining criteria, current page snapshot, chosen action, and the **actual discovered MCP function schema**, then generates *only its arguments*.

Python validates output, calls Playwright MCP, records trace evidence, and rejects unverified `finish` decisions.

**Important:** This is a newly reconstructed project, not a byte-for-byte update to the older project ZIP (which was not provided in this conversation). It includes an integration adapter for the existing Strands endpoint, but the actual endpoint payload may require adapting to your decider server. This environment does not have your local models or Playwright MCP, so only mocked integration behavior was tested.

## Quickstart: offline demo (no downloads, no API calls)

```bash
cd integrations/goal_aware_strands
python3 -m agent --demo --output demo_trace.json
python3 -m pytest -q tests  # if pytest installed
```

The demo is **scripted test doubles**, NOT real inference. Its synthetic Python website data is not current information.

## Your prior Ubuntu setup (preconfigured)

The included `config.json` matches your previously used endpoint layout:

- Ollama: `http://127.0.0.1:11434/api/chat` (native API, `stream: false`, `think: false`, `temperature: 0`).
- Goal/state: `qwen3:0.6b` in Ollama.
- Tool arguments: `qwen2.5-coder:1.5b` in Ollama. This is the **proposed coding model**, not a previously confirmed model you already had pulled.
- Strands: `http://127.0.0.1:8021/v1/systemone` using typed `choice` question with `state`, `questions`, and response `answers.next_action`.
- Strands checkpoint: `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610`.

Start your existing Strands environment:

```bash
source ~/Downloads/strands-decider-venv/bin/activate
strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --device cpu --port 8021
```

In a different terminal:

```bash
ollama serve  # only if Ollama is not already running
ollama pull qwen3:0.6b
ollama pull qwen2.5-coder:1.5b
ollama list
```

If Ollama is already running as a service, do **not** start a second `ollama serve`.

Run the local models against an offline simulated browser first:

```bash
python3 -m agent "Visit python.org and open Downloads" --config config.json --mock-browser --output traces/models_mock_browser.json
```

Then run real Playwright MCP:

```bash
python3 -m agent "Go to https://www.python.org and open Downloads" --config config.json --output traces/playwright.json
```

### Check services

```bash
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:11434/api/tags
```

The `StrandsDecider` implementation directly uses the documented `/v1/systemone` `choice` request schema. A `choice` response with `answers.next_action.choice` is parsed without a custom compatibility shim. Official protocol: https://github.com/strands-labs/strands-decider/blob/main/docs/inference.md

## Connect Playwright MCP

Install Node.js and npx, then run:

```bash
python3 -m agent "Go to https://www.python.org and open Downloads" \
  --config config.example.json --output traces/python_downloads.json
```

The harness launches `npx @playwright/mcp@latest --headless` over MCP stdio, discovers tool schemas, and calls tools through JSON-RPC. Set an absolute executable path in `mcp_command` if needed. Before running arbitrary sites, consider browser sandboxing and a trusted-site allowlist. This experimental harness does **not** include approval dialogs, domain allowlisting, robust prompt-injection defenses, or comprehensive JSON Schema validation; do not use it with sensitive authenticated accounts.

For a safe first **real-model** test without Playwright MCP:

```bash
python3 -m agent "Visit python.org and open Downloads" \
  --config config.example.json --mock-browser --output traces/mock_live_models.json
```

`--mock-browser` still calls **real local models**, unlike `--demo`.

## Flow

```
User request
   |
   v
Qwen3 0.6B: initial criteria proposal  --> Python persists immutable goal
   |
   v
Strands Decider 2B: choose action
   |
   v
Coding model: action arguments conditioned on original goal + criteria + page + exact schema
   |
   v
Python validation (required fields, basic types, target ref, URL scheme)
   |
   v
MCP tools/call -> Playwright observation
   |
   v
Qwen3 0.6B: propose observed progress and completed criteria
   |
   v
Python records evidence, updates state, repeat until verified finish / blocked / error / limit
```

## What is and isn't guaranteed

- An immutable original goal is supplied to both the decider and coding model every step.
- Browser refs are checked against snapshot strings; invalid refs are rejected.
- The coder cannot select an unrelated tool: Strands picks the semantic action, Python resolves it to one discovered tool, and the coder fills arguments.
- Explicit result evidence is recorded for claimed completed criteria; `finish` is rejected without evidence and all criteria accounted for.
- **Caveat:** A 0.6B model can incorrectly map evidence to a criterion; this remains a heuristic, **not proof** of real-world goal completion. Consider independently verifiable success predicates for production.
- JSON Schema checking covers required keys/basic types/enums and `additionalProperties`; it does not implement full JSON Schema, nested type checking, or tool side-effect policies.
- Some Playwright versions return snapshots with formats unlike the mock, or classify actions differently. Adapters can be tuned using trace JSON.
- `extract_text` currently routes through `browser_snapshot` and does not offer arbitrary page scripts.
- This example is intentionally browser-only, not a generic code-execution agent.

## Files

- `agent/goal_state.py`: Qwen goal and state updates
- `agent/models.py`: OpenAI-compatible chat and Strands HTTP adapter
- `agent/coder.py`: goal-conditioned MCP argument generation
- `agent/validation.py`: deterministic preflight checks
- `agent/tools.py`: real MCP stdio + offline mock
- `agent/runner.py`: loop, retries, auditing, finish gate
- `agent/demo.py`: clearly labeled deterministic mock models
- `agent/cli.py`: CLI
- `tests/test_agent.py`: integration and safety-unit tests

## Suggested next experiments

Compare real coding models using identical traces; measure correct grounded argument rate, repeated-action rate, true task success, and latency. For robustness, add a URL allowlist, model-independent DOM transition checks, schema-complete validation, and evidence-specific success predicates before permitting consequential browser actions.


## Diagnosing a failed step

The CLI now prints `last_error` and `failed_stage` as well as the trace location.
To inspect an older trace:

```bash
python3 -m json.tool traces/test.json | tail -80
```

Verify the services independently:

```bash
curl -s http://127.0.0.1:11434/api/tags
curl -s http://127.0.0.1:8021/openapi.json | head -c 400
```

Strands endpoints vary between server versions. The configured `/v1/systemone` adapter must match the actual server API. If the returned response differs, use the trace's `failed_stage` and `last_error` before changing the client adapter.
