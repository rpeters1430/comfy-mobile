# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A mobile-first FastAPI dashboard ("Image Studio Mobile") that runs in Docker on a NAS and proxies a ComfyUI instance on another machine. The phone UI is a single static page (`app/static/index.html`, inline CSS/JS); all ComfyUI access goes through the backend, which never exposes ComfyUI directly to the browser.

## Commands

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v          # full suite (what CI runs, Python 3.14 and 3.15)
python -m unittest tests.test_workflow_conversion -v
python -m unittest tests.test_workflow_conversion.ConversionTests.test_connected_graph_and_prompt
python -m compileall -q app                      # CI also runs this as a syntax check
docker build -t comfy-mobile .
```

`tests/test_bundled_workflows.py` is a bare pytest-style function, so `unittest discover` does not collect it. Run it with `python -m pytest tests/test_bundled_workflows.py` (pytest is not in requirements). It asserts there are exactly 30 files in `workflows/`, so update the count if you add or remove one.

Running locally: `app/main.py` uses flat imports (`from workflow_sync import ...`) and serves `/app/static/index.html` by absolute path, because the Dockerfile copies `app/` to `/app` and runs `uvicorn main:app` from there. Tests import the modules as `app.workflow_*`. Nothing imports `main.py` in tests, so keep logic in the `workflow_*` modules where it can be tested. Running outside Docker needs `cd app` and env vars, and `/` will 404 unless `/app/static` exists.

Configuration is env-only: `COMFYUI_URL`, `STUDIO_TOKEN` (required, or every endpoint returns 401), `WORKFLOW_DIR` (bundled, mounted read-only), `WORKFLOW_SYNC_DIR` (writable).

## Architecture

- **`main.py`**: every `/api/*` route calls `auth()` (constant-time Bearer token compare) first. `request()` is the only ComfyUI HTTP helper. On an HTTP error it returns 502 and passes through only ComfyUI's JSON `error`/`node_errors` fields, never raw HTML.
- **Two workflow sources**: bundled files in `WF_DIR` and synced files in `SYNC_DIR`. Synced workflow IDs carry a `synced/` prefix, which `/api/jobs` and `/validate` strip to choose the root directory. `load_workflow` enforces path containment, a `.json` suffix and a 2 MB limit.
- **Workflow formats**: *UI* (LiteGraph JSON with `nodes`/`links`, which is what the bundled `workflows/*.json` are) and *API* (`{id: {class_type, inputs}}`). UI graphs are converted to API format on every catalog, validate and job call, using live `/object_info` from ComfyUI.
- **`workflow_conversion.convert_ui`** fails closed by design: it raises `ConversionError` instead of guessing. It mirrors what ComfyUI's own frontend sends, so check behaviour against real files and ComfyUI source, not intuition:
  - Subgraphs (`definitions.subgraphs`) are inlined first by `_flatten`; inner IDs become `<instance>:<inner>`. Instance `widgets_values` line up with the subgraph's widget-type inputs in order. Nested/bypassed subgraphs fail closed.
  - Widget values map positionally over widget-type inputs in schema order, **including linked ones** (ComfyUI keeps their slot). A randomize/fixed control slot follows INTs flagged `control_after_generate`, or top-level `seed`/`noise_seed`. V3 `COMBO` uses `options`; a `COMFY_DYNAMICCOMBO_V3` value is followed by its chosen option's sub-widgets, sent as `<combo>.<sub>` keys. `COMFY_AUTOGROW_V3` inputs arrive as `<field>.<name>`. Don't "shift values to fit".
  - Mode 4 (bypass) passes through the same-typed input; mode 2 (muted) nodes are dropped along with inputs they feed. Links missing from the links array are restored only when exactly one output and one input claim them.
  - Bare model filenames resolve to a unique subfolder entry. `LoadImage.image` is not validated; jobs fill it (`image_inputs`). List widget values are sent as `{"__value__": [...]}` because ComfyUI reads bare lists as links. Preview nodes with a required socketless widget (ImageCompare) are dropped.
- **Prompt injection point**: `bind_prompt` writes `{{prompt}}` into the single top-level prompt field: an unlinked non-negative CLIPTextEncode, a `PrimitiveString(Multiline)` titled "…prompt…" (not negative/prefix/trigger), or an unlinked subgraph input named/labelled `prompt`. None means the workflow takes no typed prompt (auto-caption), which is valid; more than one fails. `workflow_jobs.apply_job` replaces the placeholder, fills uploaded images in `image_inputs` order and overrides `seed`/`noise_seed`.
- **Jobs I/O (`workflow_jobs`)**: `/api/uploads` takes a raw PNG/JPEG/WebP body, names it `mobile_<uuid>.<ext>` from magic bytes and forwards it to ComfyUI; jobs only accept those names. `/api/jobs/{id}/outputs` lists files with HMAC-signed `view` URLs (keyed off `STUDIO_TOKEN`, 6 h expiry) because `<img>`/`<video>` can't send a Bearer header; `/view` only serves files that job produced and passes Range through.
- **`workflow_sync.WorkflowSync`**: one-way pull from ComfyUI's `/userdata?dir=workflows` into `SYNC_DIR`. It allows a strict filename allowlist (`SAFE_NAME`) and at most 200 files of 2 MB each, compares SHA-256 hashes, writes atomically via `.partial` and `os.replace`, refuses symlinks, never deletes, and serializes runs with an `asyncio.Lock`. `docs/workflow-sync-plan.md` lists the planned behaviour that isn't built yet.

## Conventions

- Keep security posture conservative. The service is meant for a trusted LAN or Tailscale network only, and new endpoints should be authenticated and must not leak ComfyUI user-data beyond workflow `.json` names.
- The version string `0.4.0-dev` is duplicated in `FastAPI(version=...)` and in the `/api/health` response.
- `.env` lives on the NAS and must never be committed. Pushes to `main` publish `ghcr.io/rpeters1430/comfy-mobile:latest` and `v*` tags are published too. Renovate manages pinned dependencies, the action SHAs and the base image digest.
- README.md still says the bundled UI workflows aren't runnable. That's out of date now that conversion exists.
- `python tools/check_workflows.py` converts every workflow against `fixtures/object_info.local.json` (gitignored; refresh with `curl -o fixtures/object_info.local.json http://100.77.144.56:8188/object_info` over Tailscale). Bundled 03/04/06 (active LoRA set to `None`) and 11–13 (orphaned guider `model` link) are broken in the files themselves.
