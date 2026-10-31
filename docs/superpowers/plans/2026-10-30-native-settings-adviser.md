# Native settings adviser implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Reliable model research, diagnostics, and validated mobile settings approval, with Ollama and optional hosted providers.
**Architecture:** Bounded research adapters extract evidence; schema-constrained AI assists extraction; deterministic validation builds proposals. Server-side reviews bind approved changes to effective workflows and fixed batch seeds.
**Tech Stack:** FastAPI, httpx, Pydantic, Trafilatura, SQLite, existing vanilla JS UI.
**Spec:** docs/superpowers/specs/2026-10-30-native-settings-adviser-design.md

## Global constraints
- Manual generation remains available; research never queues a render.
- Secrets stay in environment variables; cloud AI is explicitly configured.
- Supported first release changes: base steps, CFG, sampler, scheduler and paired size where unambiguous.
- Existing workflows, presets and caption review retain compatibility.

## Review focus
- Malformed/blocked pages must produce diagnostic failures rather than empty success.
- A valid JSON response can still contain unsupported evidence and must be rejected.
- Workflow edits, seed changes and model option changes invalidate approvals.
- Hosted failures never trigger undisclosed provider switching or retries.
- Cancelled/background tasks must not mutate expired records or leak credentials.

### Task 1: Research and provider adapters
Files: app/research.py, app/adviser_ai.py, tests/test_research.py, tests/test_adviser_ai.py, requirements.txt.
Interfaces: extract_document(raw, url), settings_excerpt(text), candidates(text), async Research.gather(target), async AI.extract(target, sources).
- [x] Write regression tests for split lines, long lines, tables, variants and literal evidence; run and observe missing modules.
- [x] Implement bounded provider/source fetching, document parsing, evidence extraction and schema-constrained Ollama/OpenAI-compatible clients.
- [x] Verify mock-provider schema requests, truncated/error responses and public URL restrictions. Commit.

### Task 2: Review state and proposal service
Files: app/adviser.py, app/adviser_store.py, tests/test_adviser.py.
Interfaces: Store.put/get, Adviser.start/get/cancel/apply/check; injected graph preparation remains in API layer.
- [x] Write failing tests for cache, expiry, branch eligibility, evidence and proposals.
- [x] Implement SQLite state/cache, bounded background tasks, diagnostics and validated proposals.
- [x] Verify deterministic and AI paths, cancellation and stale approval rejection. Commit.

### Task 3: API and mobile integration
Files: app/main.py, app/static/app.js, app/static/index.html, app/static/adviser.js, tests/test_adviser_api.py, tests/adviser.test.js.
- [x] Write failing authenticated API tests proving no render during research/apply and stale snapshots rejected.
- [x] Extract shared job preparation; add authenticated adviser routes and optional approval audit.
- [x] Add accessible mobile research/results/selection/cancel flow and frozen batch seeds.
- [x] Verify UI pure helpers and existing caption/job behavior. Commit.

### Task 4: Configuration and release verification
Files: compose.yaml, README.md, docs/adviser.md, tests/browser-mobile.cjs as appropriate.
- [x] Document Ollama/OpenAI/Gemini/OpenRouter settings, model compatibility, extraction diagnostics and deployment limits.
- [x] Run complete Python/Node suites, compile checks and browser QA; review whole branch and correct important findings.
- [x] Commit and push feature branch/create draft PR if GitHub access permits; do not merge or deploy.

Execution ruling: user explicitly said "let's do it" after the concrete approach and design; proceed inline without another permission round. A dedicated fresh scratch clone and feature branch provide isolation from user work. Hosted clients are a user-authorized extension to the design, opt-in and with no automatic paid fallback.

Verification: 124 Python and 18 Node tests passed; Python compilation, JS syntax and whitespace checks passed. Reviewer findings corrected and rechecked. Browser harness extended, but full browser execution is unverified: Chromium download returned an invalid/truncated archive. Live NAS/provider calls are unverified without deployment/credentials.
