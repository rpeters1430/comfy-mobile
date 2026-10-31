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
node --test 'tests/*.test.js'                   # front-end helpers in static/presets.js
docker build -t comfy-mobile .
```

`tests/test_bundled_workflows.py` is a bare pytest-style function, so `unittest discover` does not collect it. Run it with `python -m pytest tests/test_bundled_workflows.py` (pytest is not in requirements). It asserts there are exactly 31 files in `workflows/`, so update the count if you add or remove one.

Running locally: `app/main.py` uses flat imports (`from workflow_sync import ...`) and serves `/app/static/index.html` by absolute path, because the Dockerfile copies `app/` to `/app` and runs `uvicorn main:app` from there. Tests import the modules as `app.workflow_*`. Nothing imports `main.py` in tests, so keep logic in the `workflow_*` modules where it can be tested. Running outside Docker: `cd app` and set the env vars plus `STATIC_DIR=static` (it defaults to the Docker path `/app/static`).

Configuration is env-only: `COMFYUI_URL`, `STUDIO_TOKEN` (required, or every endpoint returns 401), `WORKFLOW_DIR` (bundled, mounted read-only), `WORKFLOW_SYNC_DIR` (writable), `DATA_DIR` (writable by uid 1000; job records, thumbnails, push keys; if it is missing those features are off), `PUSH_CONTACT` (VAPID `mailto:`), `STATIC_DIR` (local runs only).

## Architecture

- **`main.py`**: every `/api/*` route calls `auth()` (constant-time Bearer token compare) first. `request()` is the only ComfyUI HTTP helper. On an HTTP error it returns 502 and passes through only ComfyUI's JSON `error`/`node_errors` fields, never raw HTML.
- **Two workflow sources**: bundled files in `WF_DIR` and synced files in `SYNC_DIR`. Synced workflow IDs carry a `synced/` prefix, which `/api/jobs` and `/validate` strip to choose the root directory. `load_workflow` enforces path containment, a `.json` suffix and a 2 MB limit.
- **Workflow formats**: *UI* (LiteGraph JSON with `nodes`/`links`, which is what the bundled `workflows/*.json` are) and *API* (`{id: {class_type, inputs}}`). UI graphs are converted to API format on every catalog, validate and job call, using live `/object_info` from ComfyUI.
- **`workflow_conversion.convert_ui`** fails closed by design: it raises `ConversionError` instead of guessing. It mirrors what ComfyUI's own frontend sends, so check behaviour against real files and ComfyUI source, not intuition:
  - Subgraphs (`definitions.subgraphs`) are inlined first by `_flatten`; inner IDs become `<instance>:<inner>`. Instance `widgets_values` line up with the subgraph's widget-type inputs in order. Nested/bypassed subgraphs fail closed.
  - Widget values map positionally over widget-type inputs in schema order, **including linked ones** (ComfyUI keeps their slot), but not `forceInput` ones, which are sockets with no saved value. A randomize/fixed control slot follows INTs flagged `control_after_generate`, or top-level `seed`/`noise_seed`. V3 `COMBO` uses `options`; a `COMFY_DYNAMICCOMBO_V3` value is followed by its chosen option's sub-widgets, sent as `<combo>.<sub>` keys. `COMFY_AUTOGROW_V3` inputs arrive as `<field>.<name>`. Don't "shift values to fit".
  - Mode 4 (bypass) passes through the same-typed input; mode 2 (muted) nodes are dropped along with inputs they feed. Links missing from the links array are restored only when exactly one output and one input claim them.
  - Bare model filenames resolve to a unique subfolder entry. `LoadImage.image` is not validated; jobs fill it (`image_inputs`). List widget values are sent as `{"__value__": [...]}` because ComfyUI reads bare lists as links. Preview nodes with a required socketless widget (ImageCompare) are dropped.
- **Prompt injection point**: `bind_prompt` writes `{{prompt}}` into the single top-level prompt field: an unlinked non-negative CLIPTextEncode, a `PrimitiveString(Multiline)` titled "…prompt…" (not negative/prefix/trigger), or an unlinked subgraph input named/labelled `prompt`. None means the workflow takes no typed prompt (auto-caption), which is valid; more than one fails. `workflow_jobs.apply_job` replaces the placeholder, fills uploaded images in `image_inputs` order and overrides `seed`/`noise_seed`.
- **Jobs I/O (`workflow_jobs`)**: `/api/uploads` takes a raw PNG/JPEG/WebP body, names it `mobile_<uuid>.<ext>` from magic bytes and forwards it to ComfyUI; jobs only accept those names. `/api/jobs/{id}/outputs` lists files with HMAC-signed `view` URLs (keyed off `STUDIO_TOKEN`, valid 24–30 h) because `<img>`/`<video>` can't send a Bearer header. `/outputs` only signs files that job produced, and the expiry is rounded to 6 h steps so URLs stay stable and the browser caches them (`immutable`). `/view` passes Range through; `&thumb=1` asks ComfyUI for a WebP copy (`preview=webp;80`, images only), which the cards and gallery use.
- **Model controls (`workflow_controls`)**: `find_controls` reads the converted graph for `CheckpointLoaderSimple`/`UNETLoader` model pickers and existing `LoraLoader(ModelOnly)` nodes; `/validate` returns them as `controls`. Jobs may send `models`, `loras` and `extra_loras`, and `apply_controls` only accepts nodes it found and names in ComfyUI's own option lists. Extra LoRAs are `LoraLoaderModelOnly` nodes (`mobile_lora_N`) inserted after the single model loader and any LoRAs already chained to it; with more than one model loader, adding is refused. Strength 0 on a `LoraLoader` zeroes both strengths so ComfyUI skips loading it. It also exposes plain `steps`/`cfg`/`sampler_name`/`scheduler` on KSampler-type nodes (`denoise` only on refine passes, i.e. denoise < 0.99) and the size of `Empty*LatentImage` nodes with plain width/height, or with width/height linked from a `GetImageSize` (image-to-prompt-to-image workflows; `size_from_photo`, budget from `ImageScaleToTotalPixels.megapixels`, and a job `size` replaces the links); jobs send `sampling` and `size`, range-checked server-side. The phone picks a shape and keeps the workflow's pixel count. Exact-resolution wallpaper presets also send `fit`: `_apply_fit` puts an optional `ImageUpscaleWithModel` and a lanczos center-crop `ImageScale` in front of every `SaveImage(Advanced)`, so the model renders near its native pixel count and the saved file is exactly the screen size. Auto-caption VLM nodes (`Qwen3VLAbliteratedPromptPair` and kin) expose `model`/`caption_type`/`length`/`content_mode` (validated against ComfyUI's options) and their boolean switches as `captions`; jobs send `caption`. The phone shares caption settings across workflows except the caption style.
- **Job control**: `/api/jobs/{id}/cancel` deletes a pending job, or interrupts it only if it is the running one (older ComfyUI ignores `prompt_id` on `/interrupt` and would stop anything). `/api/comfy/free` asks ComfyUI to unload models. These ComfyUI endpoints return empty bodies, hence `request(..., expect_json=False)`.
- **Job store and push (`job_store`, `push`)**: `/api/jobs` records each job in `DATA_DIR/jobs.json`. A background task (`watch_jobs`, started in `lifespan`) polls `/history` for unfinished ones, stores the `job_summary`, keeps a WebP thumbnail of the cover image and sends Web Push. `/outputs` answers finished jobs from the store and `/view?thumb=1` serves the stored thumbnail, so the gallery survives ComfyUI restarts (ComfyUI keeps history in memory only). Push only posts to the browser vendors' push hosts (`PUSH_HOSTS`), never to arbitrary client URLs, and drops subscriptions that return 404/410. Web Push needs the app opened over HTTPS.
- **Live progress (`progress`)**: `listen` keeps a websocket to ComfyUI's `/ws` as `CLIENT_ID` (the id jobs are queued under, since ComfyUI sends progress only to that client; a second connection with the same id replaces the first). `ProgressTracker` keeps step counts per prompt and the latest preview frame, which binary frames attribute to the currently executing prompt. `/api/jobs` registers each prompt's sampler nodes (`expect`), so status reports `pass`/`passes` (two-pass Wan runs), drops stale steps once another node runs, and sets `finishing` after the last sampler (decode/save). The phone polls `/api/jobs/{id}/progress` and fetches `/preview` when `frame` changes. Previews only arrive if ComfyUI's live preview method is on.
- **PWA**: `static/sw.js` handles the Web Share Target (`POST /share-target` → Cache Storage → `/?shared=N`), push messages and notification clicks; it never caches the app or API. The server-side `/share-target` route is only a fallback before the worker is installed. Saved prompts and the gallery live in `localStorage` (per device).
- **`workflow_sync.WorkflowSync`**: one-way pull from ComfyUI's `/userdata?dir=workflows` into `SYNC_DIR`. It allows a strict filename allowlist (`SAFE_NAME`) and at most 200 files of 2 MB each, compares SHA-256 hashes, writes atomically via `.partial` and `os.replace`, refuses symlinks, never deletes, and serializes runs with an `asyncio.Lock`. `docs/workflow-sync-plan.md` lists the planned behaviour that isn't built yet.

## Conventions

- Keep security posture conservative. The service is meant for a trusted LAN or Tailscale network only, and new endpoints should be authenticated and must not leak ComfyUI user-data beyond workflow `.json` names.
- The version string lives in `VERSION` in `main.py` (used by FastAPI and `/api/health`); the README title repeats it.
- `.env` lives on the NAS and must never be committed. Pushes to `main` publish `ghcr.io/rpeters1430/comfy-mobile:latest` and `v*` tags are published too. Renovate manages pinned dependencies, the action SHAs and the base image digest.
- `python tools/check_workflows.py` converts every workflow against `fixtures/object_info.local.json` (gitignored; refresh with `curl -o fixtures/object_info.local.json http://100.77.144.56:8188/object_info` over Tailscale). All 31 bundled workflows pass. A `LoraLoaderModelOnly` left on `None` is bypassed (mode 4) in the files rather than special-cased in the converter.


## Mobile workflow tools (v0.5.0)

- `preset_store.py`: SQLite preset CRUD with transaction-protected writes, maximum 100 presets / 64 KB each. `/api/presets` persists full settings snapshots without uploaded filenames. `/validate` checks live options before UI load.
- `JobStore` now serializes mutation with an RLock (one uvicorn process), saves `params` on new records and exposes paginated `/api/library/jobs`. Legacy records may lack params. Job inputs are served by authenticated job/index lookup only; bounded WebP previews live in `/data/inputs`.
- `caption_review.py`: identifies a single supported VLM node, builds ancestor-only graphs with PreviewAny sinks for positive/negative text, or replaces those text links for approved rendering and prunes unused dependencies. `/api/jobs` takes `stage: caption|render` and optional `review: {positive, negative}`. `/validate` exposes capability/reason. Fail closed on ambiguity/non-text consumers.
- `wake.py`: administrator-configured `WOL_MAC` / `WOL_BROADCAST`, UDP port 9, 30-second cooldown. GET/POST `/api/comfy/wake` are authenticated. Never accept client-selected destinations.
- `static/mobile-tools.js`: pure data helpers, loaded before app.js; Node tests cover NAS/local merge and presets. `tests/browser-mobile.cjs` is optional Playwright smoke coverage with API mocks (not part of the dependency-free Node test glob).


## Settings check

- `settings_check.check(graph, controls)` runs fixed per-family rules over the final job graph (`POST /api/jobs/check` reuses `prepare_job`, so it sees exactly what Generate sends). It needs no AI or setup, unlike the adviser. Families come from the model folder (`SDXL\`, `Pony\`, `SD15\`, `Krea2\`…), then filename keywords. Unknown families get no family advice. Rules: LoRA/model family mismatch, duplicate LoRAs, strength > 1.5, speed models (turbo/lightning/DMD/`Nstep`) with too many steps or CFG, SD1.5/SDXL step, CFG and size ranges, and Pony score tags. Fixes are offered only on plain `KSampler` fields that `find_controls` exposes. All bundled workflows produce no issues at their defaults; keep it that way when adding rules.
- The phone calls it on Generate when "Check settings before generating" is ticked (stored per device). It skips the check when an adviser approval is active, and a failed check never blocks the job.

## Skip face detailer

- `find_controls` lists `FaceDetailer(Pipe)` nodes whose only used output is the image as `detailers`. A job's `skip_detailers: true` removes them and wires their input image to their consumers. On workflow 02 this cuts roughly 1–2 minutes per detected face. The phone shows it as "Skip face detailer" under Models & settings.

## Gallery detail screen (v0.5.1)

Gallery taps use `openGalleryPhoto` and fetch fresh signed output URLs; they do not switch to the Create tab. The full-viewport dialog supports metadata, paging through files, optional comparison, video playback, and a link to the existing job card. `run_details` records effective values only (never the option lists) in each new JobStore record; create/history APIs return this optional `details` field. Legacy records remain readable. `MobileTools.preferredWorkflows` hides bundled copies only when the synced copy is runnable, exposing a labelled bundled fallback when sync is broken.
