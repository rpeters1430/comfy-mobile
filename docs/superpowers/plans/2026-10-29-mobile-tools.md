# Mobile workflow tools Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task, followed by a fresh whole-branch review.

**Goal:** Ship NAS gallery/presets, caption review, PC wake and image comparison.
**Architecture:** Extend job records, add small preset/caption/WOL modules, integrate authenticated APIs into existing static UI.
**Tech Stack:** Python/FastAPI, SQLite (stdlib), vanilla JS/CSS, unittest/pytest and Node.
**Spec:** docs/superpowers/specs/2026-10-29-mobile-tools-design.md

## Global Constraints
- Preserve workflows, legacy job records, and security fixes from PR 33.
- Require bearer authentication; no client-controlled network destination.
- Avoid new runtime dependencies; remain usable on a 256 MB NAS container.

## Review Focus
- Legacy jobs lack rerun parameters or input previews: display them without crashes.
- Concurrent preset writes and job watcher writes must not corrupt data.
- A stale preset must explain validation problems before generation.
- Caption-only execution must never include diffusion/model-loading branches unnecessarily.
- ComfyUI offline or WOL unconfigured must leave NAS gallery/settings usable.

### Task 1: Shared gallery and presets
Files: app/job_store.py, app/preset_store.py, app/main.py, tests/test_mobile_tools.py.
- [x] Write/run failing persistence, legacy-history pagination, CRUD and authentication tests.
- [x] Extend JobStore.add with optional params; add list_jobs(limit,offset), input preview paths, serialize mutations.
- [x] Implement PresetStore list/save/delete using SQLite transactions and bounded records.
- [x] Add GET /api/library/jobs and preset CRUD/validation routes; store rerun parameters on job submission.
- [x] Run tests and commit.

### Task 2: Caption review
Files: app/caption_review.py, app/main.py, tests/test_caption_review.py.
- [x] Write/run failing tests for ancestor-only extraction, positive/negative override, ambiguous graphs and dimension consumers.
- [x] Implement capability(graph,info), caption_graph(graph,info), apply_review(graph,info,positive,negative).
- [x] Expose capability in validation and stage/review in jobs; validate final transformed graphs.
- [x] Run tests and commit.

### Task 3: PC wake and comparison inputs
Files: app/wake.py, app/main.py, app/job_store.py, compose.yaml, tests/test_mobile_tools.py.
- [x] Write/run failing magic-packet, cooldown, destination validation and job-bound input tests.
- [x] Add authenticated wake status/action; store/fetch bounded WebP input previews and serve by job/index.
- [x] Document environment configuration and Docker broadcast limitations.
- [x] Run tests and commit.

### Task 4: Phone UI
Files: app/static/index.html, app/static/app.js, app/static/style.css, app/static/mobile-tools.js, tests/mobile-tools.test.js.
- [x] Write/run failing pure-helper tests for NAS/local history merging, preset validation and caption-stage completion.
- [x] Integrate gallery pagination, shared preset save/load/delete, staged caption editor, wake/startup polling and image comparison dialog/zoom.
- [x] Check stale async requests, logouts, missing-storage errors and full-size output viewing.
- [x] Run JS/Python suites plus a browser smoke test and commit.

### Task 5: Documentation and verification
Files: README.md, docs/security-review.md, CLAUDE.md.
- [x] Update feature/configuration docs and version.
- [x] Run full Python/Node suites, compileall and diff checks; independent branch review; fix important findings.
- [x] Publish a feature branch and reviewable PR, without merging/deploying.

## Execution record

- Base: merged security PR 33, origin/main 4af171e.
- Implemented inline with a fresh final reviewer. No deployment or merges.
- Shared interface checks: Job.params supplies gallery reruns and input lookups; Job.stage/review supplies caption preview/render; full preset snapshots validate before UI restore.
- Decisions: existing single-process Docker deployment retained; SQLite only for presets, no new Python runtime dependencies; unsupported caption graphs fail closed; comparisons use first input preview.
- Verification results and final review fixes are recorded in the PR description.

- Final review fix: validate and return the exact current preset snapshot; the phone loads that snapshot instead of its potentially stale cached record. API regression was RED then GREEN; browser verifies a second-device preset update is loaded.
- Final review fix: preserve selected workflow identity across catalog refresh so in-flight settings loading can complete. Browser regression reproduced controlsReady=false before the fix and passes after it.
- Browser-discovered fix: reopening an already-open job from Gallery now switches to Create before scrolling. Browser test failed on the invisible job card before the fix and passes after it.
- Final evidence: full Python suite, Node helper tests and mocked mobile Chromium flow. No real WOL packet or live custom-node run was sent.
