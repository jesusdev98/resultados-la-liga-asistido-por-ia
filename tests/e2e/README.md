# Test the actual local Qwen model

`real-qwen.cjs` drives an isolated Chrome profile through the real load-model and
chat controls. It never substitutes model completions or API responses. A safe
rejection is recorded as a failed answer, not as a successful conversation.

## Run on Windows

Keep dependencies, the browser profile and generated evidence outside the project:

```powershell
$env:LALIGA_TEST_HOME = "$env:LOCALAPPDATA\Temp\opencode\prueba-deepseek-e2e"
New-Item -ItemType Directory -Force $env:LALIGA_TEST_HOME | Out-Null
Copy-Item tests/e2e/package.json "$env:LALIGA_TEST_HOME\package.json"
npm.cmd --prefix $env:LALIGA_TEST_HOME install
# Start the Python application separately with its existing virtual environment.
node tests/e2e/real-qwen.cjs
```

Chrome must be installed and expose a real WebGPU adapter. The first run may
download approximately 1 GB of Qwen weights. The profile is dedicated to these
tests; it never attaches to a running user browser or imports cookies/storage.
Runs use one browser/model sequentially, with no automatic retries or unsafe GPU
flags. Existing downloaded weights may be reused on subsequent runs.

To run only the reported Madrid conversation, set
`$env:LALIGA_CASE_IDS = 'following-three-exact-user'` and
`$env:LALIGA_RUN_TIMEOUT_MS = '300000'` before running. This executes the result,
implicit next match, and following-three turns exactly once. Remove the case
selection to use the whole matrix; `LALIGA_CASE_LIMIT` bounds the selected cases.
`LALIGA_BASE_URL` optionally
selects the loopback application URL (default `http://localhost:8000`).

## Read the evidence

Each timestamped `runs/` directory contains:

- `results.json`: preflight, model-load timing, case outcomes, tool evidence and
  real completion text for synthetic test questions, including rejected prose.
- Failure screenshots and bounded console/page errors.
- `browser-v8-coverage.json` and `coverage/`: Chromium application-JavaScript
  coverage and an HTML report, when the run reaches coverage collection.

The opt-in `__LALIGA_DIAGNOSTICS__` callback observes completions and validation
codes without changing them. Normal application sessions have no observer and
write no diagnostic files. Do not enable this observer for private conversations.

Coverage percentages describe only the listed code and executed paths. They do
not prove exhaustive language understanding or zero hallucinations. CDN/model
internals and CSS/layout correctness are not included in JavaScript coverage.
Case pass rates, model failures and code coverage are reported separately.
The browser collector checks that every production JavaScript module was loaded;
missing modules block the report instead of silently shrinking its denominator.
V8-to-Istanbul percentages are not interchangeable with syntax-instrumented
statement/branch metrics from the deterministic suite below.

### Recorded latency probe

One real Intel WebGPU/Qwen run per configuration for the same three-Atlético-fixture
question measured 52.6 seconds initially, 41.3 seconds with a compact grounded
selector, and 28.2 seconds after also compacting fixture-writer prompts. Each run
passed independent evidence checks. Model-load time was measured separately.
These are individual observations, not a statistically established benchmark;
latency and model correctness can vary. The 30-case matrix was stopped at the
user's request to prioritize these short latency probes and has not completed.

To recheck retained evidence without rerunning or simulating the model:

```powershell
node tests/e2e/verify-evidence.cjs <path-to-results.json>
```

These independent checks assert the requested club/pair, actual returned club,
strict chronological order, advancement beyond the displayed event, explicit
calendar cutoffs, exact confirmations, and fixture coverage in the model prose
itself (not merely in its supporting cards). Existing failed cases remain failed.

## Deterministic checks

```bash
.venv/bin/python -B -m unittest discover -s tests -v
node --test web/agent.test.mjs
```

Python coverage requires `coverage` in the project virtual environment. Set
`COVERAGE_FILE` to an external path and use `coverage run --branch` with explicit
sources `laliga,resolve,tools,panel,server,mcp_server`, then `coverage report` and
`coverage json -o <external-path>`. HTTP tests patch only upstream data and remain
separate from real-model proof. Using system Python without the optional MCP
dependency does not reproduce the supported virtual-environment test setup.

### Coverage gates

The six backend modules reached 100% statements and branches in the recorded
61-test execution (1,171 statements and 436 branches, zero exclusions). Reproduce
the threshold with `coverage report --fail-under=100`; this is backend coverage,
not proof that Qwen answers correctly.

For original-source frontend coverage with isolated DOM and engine boundaries:

```powershell
$env:LALIGA_TEST_HOME = "$env:LOCALAPPDATA\Temp\opencode\prueba-deepseek-e2e"
node tests/frontend/run.cjs
```

The runner instruments every production `web/*.js` module, seeds unloaded files
at zero, checks source identity during the run, and writes JSON/HTML reports under
the external test home's `frontend-coverage/runs/` directory. It exits nonzero if
any test fails or any statements, branches, functions, or lines remain uncovered.
An incomplete report is not a 100% result. Node's experimental VM-module warning
is expected with Node 22. Test-engine responses do not establish model quality;
use the actual Qwen track above for that independent evidence.

The recorded source-instrumented run passed 36 deterministic frontend tests and
reached 100% statements, branches, functions and lines across `agent.js`, `app.js`,
`match-cards.js`, `mcp-ui.js`, and `webmcp.js`, with no exclusions. The stateless
match-card module retains invalid-input and safe-logo checks; those checks are
tested directly rather than removed. This result excludes CSS, external SDK/model
internals and the server-generated iframe script; layout and real-model quality
still need their separately reported checks.

## Structured fixture narration

Fixture writers use the local model's JSON-schema decoding, with two required
string fields and no extra properties: `respuesta` (the model-written sentence)
and `estado` (the source-backed date/time status generated under a single-value
enum). Input projects the source teams, day, time and expected status into one
fixture at a time. Other tool writers keep their existing plain-text contract.

The application strictly decodes the model response and displays its two strings;
it never fills a missing field or replaces failed narration with a factual
template. The verbatim day must occur in the sentence; multiple-fixture sentences
must also name both teams. A grounded singleton can use the existing contextual
form `el próximo partido` without repeating club names. Existing
time/date, JSON-echo and contradictory-status checks still apply. Accented status
claims such as `fecha está confirmada` are checked too. Multi-fixture publication
remains atomic, with one shared targeted correction and no tool requery.

The added structured-response tests raise the deterministic frontend count to 40;
the remeasured five-module coverage gate remains 100% in all four dimensions.
Malformed envelopes, instruction echoes, wrong status, invented times, missing
teams/dates and exhausted corrections are tested as failures, not synthesized
successes. Only the separately recorded actual-Qwen run establishes whether this
contract works with the loaded model.

### Latest actual-model outcome

Run `2026-10-28T00-33-06-057Z` passed the result and contextual next-match turns,
but failed the following-three turn. Tool selection, count and chronology were
correct; the first fixture narration repeated a contradictory provisional-status
claim until the 160-token limit, leaving incomplete JSON. The parser rejected it
and no partial cards/prose were published. This contract is **not yet reliable
end to end** with the observed Qwen 1.5B run. No further automatic retry was made.
