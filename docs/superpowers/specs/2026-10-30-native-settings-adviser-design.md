# Native settings adviser: first release

Status: user approved implementation on 2026-10-30. Implemented on the feature branch; see docs/adviser.md for the shipped first-release boundaries and configuration.

User-authorized extension: support explicitly selected OpenAI, Gemini, OpenRouter and compatible structured-output endpoints as alternatives to local Ollama. Credentials stay in the NAS environment; no automatic paid fallback.

## Intent and decisions

Add model research and a settings adviser to Comfy Mobile's existing FastAPI backend and phone UI. The user selected the recommended native integration over routing through Open WebUI or adding a separate service. Reuse the useful research and validation ideas from the supplied Image Studio 5.1.0 function without importing or executing that Open WebUI export at runtime.

This is a multi-module feature. The first release covers research, evidence cards, and approval of settings changes. Goal presets, caption rewriting, automatic benchmarking, and comparison renders are follow-up work. These scope boundaries and the implementation defaults below are the designer's proposed calls, not additional user requests.

## Existing behavior and constraints

Comfy Mobile v0.5.1 converts bundled/synced UI workflows into API graphs using live object_info. workflow_controls discovers and validates per-run models, LoRAs, sampler values, caption options and size. main.py applies those choices before posting the resulting graph to ComfyUI. Existing shared presets and gallery details retain effective run settings.

The attachment contains an Open WebUI Python Pipe with SearXNG/Civitai/Hugging Face research, evidence extraction, caching, variant checks and chat-based approvals. Its defaults identify SearXNG at https://searxng.tailb94d9.ts.net and Ollama at http://192.168.8.143:11434. They do not prove current deployment connectivity or the installed adviser model. The NAS runs orchestration; AI inference stays on the PC. No new container or model download is required.

## Approaches considered

1. Native backend adviser, selected: shares the existing control validation, UI, presets and gallery. Requires extracting research into small modules.
2. Call Open WebUI: greater initial reuse but introduces another runtime dependency and chat approval state.
3. Separate adviser container: reusable by both clients but increases deployment and maintenance work.

## Extraction reliability comes first

User clarification: SearXNG previously found pages, but page interpretation and recommendation extraction failed. Successful search is not acceptance evidence for this feature. Before porting the adviser, reproduce a real failing model/source example and trace discovery, fetch, content extraction, model matching, AI response parsing, evidence validation and proposal generation separately.

Local probes against methods extracted unchanged from the attached function reproduced two concrete weaknesses: _clean_recommendations accepts "Sampling steps: 25" but rejects "Sampling steps:\n25" with identical verified-source metadata; _settings_excerpt keeps only the first 400 characters of a matched line and therefore loses settings appearing later in that line. These are demonstrated code limitations, not a confirmed diagnosis of the user's historical run. The extraction request also caps output at 500 tokens despite requesting a large JSON object with evidence; truncation is a hypothesis to investigate using response completion metadata and actual output.

First milestone: diagnostic results that report each source's fetch status, extracted text length, retained settings context, identity decision, AI completion/parse outcome, and exact reason each candidate is accepted or rejected. Keep bounded technical diagnostics free of secrets and images. Distinguish no published settings from extraction failure and from settings already within recommendation ranges.

Preserve document structure and normalize table/label-value layouts before extraction. Select evidence windows around matches instead of clipping the start of long lines. Prefer provider structured fields/model cards when available. Retain original evidence and allow whitespace-normalized quotes without relaxing model identity or inference-vs-training checks. Use a constrained response schema with sufficient bounded output budget; detect incomplete responses and report them distinctly. Add deterministic candidates for explicit settings, then have AI interpret ambiguous context within evidence checks. Include regression fixtures for split lines, tables, long lines, model variants, blocked/JS-only pages, and truncated/invalid AI responses. A live failing example must pass extraction and evidence checks before claiming the NAS issue is fixed; the native integration can be verified with fixtures independently.

## User flow

The Create form offers Research & review settings as an optional action. Current workflow, model and LoRA choices are captured after normal validation. Research runs without queueing a ComfyUI prompt. The UI shows research progress and supports cancel and manual refresh.

Completed results contain model cards with exact filename, matched version, identity strength, source title/link, short supporting excerpt, retrieval time, confidence and any limitation. Suggestions list node/pass, setting, current value, proposed value and evidence. Users select individual changes; ambiguous coupled changes are either grouped atomically or withheld. Buttons are Apply selected to form, Keep current, and Cancel. Applying changes does not queue an image; the normal Generate action remains explicit.

Supported suggestions initially cover steps, CFG, sampler, scheduler, and latent width/height through existing validated controls. Resolution is a paired change and preserves a requested output wallpaper fit. Do not alter model selections, LoRA strengths, denoise, prompts, captions, seeds or graph topology in this release. These can appear as informational source notes, never executable patches.

After applying, the form shows a short confirmation and ordinary controls remain editable. Any subsequent edits invalidate the approval association; generation still works manually. No evidence or conflicting evidence produces an explanation, not invented recommendations. Service failures leave the existing generation path available.

## Backend boundaries

Extract a shared prepare-job function from the existing job route so research, approval and generation resolve workflows, conversion, controls and caption-review substitutions identically. Preserve existing call order and errors. Research examines the effective graph including added LoRAs, not the original saved workflow.

Keep new logic out of main.py where practical:

- model_research: bounded source discovery, identity matching, excerpts and deterministic extraction.
- settings_adviser: Pydantic evidence/proposal models, Ollama structured extraction, capability checks and proposal construction.
- adviser_store: SQLite research cache, task state and expiring approval records under DATA_DIR.
- adviser UI helpers: render evidence safely and manage form revisions; integrate into existing static app assets.

New authenticated routes, proposed naming:

- GET /api/adviser/status: configured/reachable service state and configured model availability, with bounded checks; redact credentials.
- POST /api/adviser/reviews: accept an existing Job-shaped request plus refresh flag and optional existing review text; prepare but do not execute; return a random review ID.
- GET /api/adviser/reviews/{id}: phase, results, warnings and expiry.
- DELETE /api/adviser/reviews/{id}: cooperative cancellation.
- POST /api/adviser/reviews/{id}/apply: selected proposal IDs plus the current Job-shaped request; verify the effective snapshot and return validated form overrides and an approval token.

POST /api/jobs gains an optional adviser approval token for audit association. It revalidates the rebuilt graph against that token when supplied. The approved settings are stored server-side, never trusted as arbitrary client patches. With no token, the normal manually configured generation path remains valid. Existing caption review remains separate and works unchanged.

## Research, model identity and branch mapping

Collect active model and LoRA targets from branches reachable from supported output nodes. Ignore disabled LoRAs; deduplicate files while preserving associations to model branches. Permit up to 16 targets, process base models first, and visibly report omitted targets if the limit is reached. Request limits and a total task deadline bound worst-case work.

Use author/model-card sources from Civitai and Hugging Face plus official node documentation where relevant. SearXNG discovers additional sources; community settings are labelled as examples. Use exact model/version IDs or hashes when already available, without reading PC files or adding a custom ComfyUI extension. Otherwise classify filename/version matching explicitly; renamed or ambiguous files remain unverified. Never label a fuzzy match as verified.

Preserve model variant tokens such as Turbo, Lightning, DMD and distilled. Separate training settings from inference settings. Every executable suggestion requires a supporting excerpt tied to the matched model/version and an unambiguous model-to-sampler path. Community-only or uncertain identity can produce information cards but no executable changes. Conflicts are shown; do not average incompatible settings.

Trace model links through recognized pass-through model/LoRA nodes. Unknown wrappers, ambiguous multiple model branches, refiners, detailers and video high/low-noise splits remain read-only until branch-specific support is implemented. Initial executable support is an unambiguous base sampler branch, including recognized split sampler/guider/scheduler chains. Validate every changed field through existing controls and live object_info. Resolution suggestions require unambiguous applicable latent controls. Unsupported graphs retain research cards and a clear limitation.

## AI and network behavior

Configuration stays environment-only: SEARXNG_URL, OLLAMA_URL, ADVISER_MODEL and optional CIVITAI_API_KEY; wire them through compose with empty defaults. The NAS operator can use the attachment's URLs, a reachable LAN address or shared Docker DNS. ADVISER_MODEL must name an already installed model; never silently select or download one. Status distinguishes missing config, unreachable service, missing JSON output, empty results and missing Ollama model.

Send only model names and technical search terms to SearXNG. Do not send images, prompts, tokens, uploaded names or full graphs to external research sites. Send only bounded technical context and retrieved excerpts to local Ollama. Treat excerpts as untrusted data and parse responses into a strict schema; reject unknown fields, non-finite numbers, invalid options, unsupported node IDs and unsupported proposals. Evidence must support the exact value or range; the LLM cannot authorize graph mutations.

Use async HTTP with bounded streamed bodies, connect/read deadlines, bounded redirects and a global per-task deadline. Configured SearXNG/Ollama origins may be private; fetched public source URLs must reject private, loopback, link-local and reserved destinations at connection time and at every redirect, including DNS rebinding defenses. Never forward auth headers between origins. Prefer public source adapters over unrestricted page fetching. Escape rendered text, allow only HTTP(S) source links and use safe link attributes.

Designer defaults: one active review per studio, up to 2 queued reviews, one concurrent Ollama request, 8 search results per query, maximum 5 source excerpts per model, 256 KB response bodies, 600-character evidence excerpts, and a 180-second overall review deadline. Report partial results on deadline. Cancellation stops scheduling additional work and discards late results. These limits are configurable implementation constants, not user-facing tuning requirements.

## Cache and approval integrity

SQLite writes are transactional. Cache by provider identity/version or model filename identity, ComfyUI origin and extraction schema version, keeping variants separate. Default positive TTL is 30 days and negative/error TTL 1 hour. Include source retrieval timestamps, identity confidence and schema version. Manual refresh bypasses freshness, deduplicates in-flight work and replaces records only after a validated result. Retain at most 512 research records with bounded record size.

Persist random review IDs and server-side proposals with a 10-minute expiry, scoped to the current studio credential fingerprint. Snapshot identity covers workflow source/content, converted effective graph, model/LoRA choices, sampler/size/caption choices, prompt and input references plus relevant capability schemas. Compare a canonical hash on apply and again on Generate. Do not canonicalize away meaningful inputs; do not freeze transient progress/output metadata into the hash. Seed handling must reuse an explicit seed captured when review starts, avoiding a random-seed mismatch on submission.

Batch decision (designer default, resolving builder review): a review covers the existing 1–8-run batch. Capture the run count and explicit ordered seed list when review starts, resolving random/sequential policy once. The review creation request carries this batch envelope alongside the Job-shaped settings. Bind the approval to that ordered list plus the shared effective graph/settings; validate each Generate request against its declared batch index and captured seed. A single-run request is a one-item batch. Changing count, seed policy, seed list or any shared setting invalidates the approval and requires re-review or an explicit manual run. Every submitted item records the same review ID and its batch index. This is authorization/audit binding, not a new rendering queue or an idempotency promise.

Applying a selection creates an immutable approved snapshot and token. Browser receives validated field overrides only. Editing invalidates the token locally; server checks remain authoritative. Approval records survive a process restart until expiry, but active research is marked interrupted. Do not claim request idempotency for render submission: existing Generate behavior remains unchanged. Record review ID, selected deltas, evidence IDs and timestamps in new job details only after successful ComfyUI submission. Legacy jobs and presets remain readable; presets store settings without live approval tokens.

## Verification and rollout

Use mocked SearXNG, source sites, Ollama and ComfyUI for automated checks. Cover exact/wrong variants, fuzzy identity, training-value rejection, source conflicts, active/disabled LoRAs, unsupported branches, malformed AI JSON, timeouts, SSRF/redirect cases, cache expiry/refresh, cancellation, tampered proposals, changed workflows/capabilities and restart/expiry behavior. Integration tests exercise auth and prove review/apply make no /prompt call. Test that only explicit Generate queues a validated graph, both with valid approvals and without adviser use.

Frontend checks cover selection, grouped changes, stale form edits, partial results, cancellation and unavailable services. Run existing Python/Node suites and compile checks; retain workflow conversion and caption-review regression coverage. Verify a phone-size browser flow with mocked APIs. Real NAS/PC checks are a separate deployment verification: service reachability, installed model, one sourced review, keep-current behavior, approved settings in gallery details and offline fallback. Do not claim those passed from mocked tests.

Ship configuration/docs and optional UI together; adviser stays inactive until configured. Keep existing data volumes and manual generation intact. Deliver implementation through a feature branch and reviewable PR after this design is approved; publishing a new main image or updating NAS containers is outside this design step.

## Open implementation choices

Builder may choose source-adapter internals, exact schema/class names, SQLite migration details and compact UI styling within the existing app. Do not expand executable settings or workflow families without explicit validation coverage. No unresolved product decision is required to plan this first release.
