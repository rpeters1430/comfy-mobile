# Roadmap: every workflow runnable from the phone

Status as of 2026-10-28. Companion to `workflow-sync-plan.md`, which covers syncing only.

## Where we are

| Area | State |
| --- | --- |
| Auth, health, queue, history list | Working |
| Sync from ComfyUI `/userdata` | Working, still to be confirmed against the real PC |
| UI → API conversion | Working: subgraphs, bypass/mute, V3 COMBO / dynamic combo / autogrow, model subfolders, link repair. 24/30 bundled workflows convert |
| Prompt binding | Done: one prompt field found automatically; auto-caption workflows take none |
| Image inputs | Done (backend): `POST /api/uploads` and `images` on jobs |
| Viewing results | Done (backend): `/api/jobs/{id}/outputs` with signed, Range-capable view URLs |
| Phone UI | Done (first version): filtered workflow cards, image slots with on-device resize, live job card, results with video and captions, gallery |
| Broken bundled files | 03/04/06 (active LoRA set to `None`), 11–13 (orphaned guider `model` link). Re-save from ComfyUI or use the synced copies |

### Workflow groups

| Group | Workflows | Needs |
| --- | --- | --- |
| A. Flat text-to-image | 01, 14, 15, 17, 19, 21, 23 | Phases 0, 1, 2, 4 |
| B. Flat image-to-image / image-to-prompt | 02 (both), 07, 16, 18, 20, 22, 24, 26 (I2P), 27 | + Phase 3 |
| C. Subgraph image | 03, 04, 05, 06, 25, 26 (I2I), 28 | + Phase 5 |
| D. Subgraph video | 08, 09, 10, 11, 12, 13 | + Phase 5, video output |

Custom nodes in use (Booru Tagger, Qwen3VLAbliteratedPromptPair, FreeOllamaVRAM, rgthree, pysssss, Impact FaceDetailer, RIFE VFI) must be installed on the PC. The converter checks for them against live `/object_info`.

## Phase 0: ground truth (do first)

Done. To refresh the snapshot (ComfyUI is reachable over Tailscale):

```sh
curl -o fixtures/object_info.local.json http://100.77.144.56:8188/object_info
python tools/check_workflows.py                       # bundled workflows
python tools/check_workflows.py fixtures/object_info.local.json path/to/workflow-sync
```

Remaining failures as of this snapshot, counted by first error: subgraph (10), needs an uploaded LoadImage (6, and three of those also contain subgraphs), and auto-caption workflows with no typed prompt (7).

## Phase 1: converter correctness for flat graphs

1. **Node modes.** Drop mode 2 (muted) nodes and anything that only feeds into them. For mode 4 (bypassed) nodes, reconnect each downstream link to the bypassed node's input of the same type, which is how ComfyUI does it. Fail closed if the type match is ambiguous.
2. **Widget count check.** If `widgets_values` is shorter than the expected number of slots, raise instead of falling back to defaults without saying so.
3. **Legacy `PrimitiveNode`** (frontend-only, not in `/object_info`): substitute its value into the target input. Synced workflows from older ComfyUI versions may still use it.
4. **Node IDs** that aren't plain integers (needed once subgraph IDs like `12:5` exist): make the dangling-link check stop relying on `isdigit()`.
5. **Unit tests** for each case, built from small extracts of the bundled workflows.

## Phase 2: workflow parameters (replaces `{{prompt}}`-only)

Each workflow declares what the phone may set.

- **Convention:** a node titled `📱 <Label>` in ComfyUI becomes an exposed parameter, e.g. `📱 Prompt` on a `PrimitiveStringMultiline`, `📱 Seed`, `📱 Image` on a LoadImage, `📱 Width`. Titles travel with the workflow, so syncing picks them up with no extra files.
- **Fallback:** a parameter file `<workflow>.mobile.json` on the NAS for workflows you'd rather not edit.
- **Backend:** `GET /api/workflows/{id}/parameters` returns typed fields (string/int/float/bool/enum/image) with the default, min and max from `/object_info`. `POST /api/jobs` takes `{workflow_id, params: {...}}` and validates every value against the schema.
- **Compatibility:** keep `{{prompt}}` working as a shorthand for one prompt parameter.
- **Seed:** keep the per-job override. Also cover `RandomNoise.noise_seed`, which subgraph workflows use.

## Phase 3: image inputs

1. `POST /api/uploads`: authenticated, PNG/JPEG/WebP only, size cap, re-encode or sniff magic bytes, forward to ComfyUI `/upload/image` with `type=input` and a generated filename (never one supplied by the client).
2. The job sets each `📱 Image` LoadImage's `image` input to the uploaded name.
3. On the phone: camera/gallery picker, preview, and multiple slots for workflows 05, 06 and 28.

## Phase 4: outputs, progress, gallery

1. `GET /api/jobs/{id}/outputs`: list images, videos and text (`PreviewAny` / `ShowText` captions) from `/history/{id}`.
2. `GET /api/view?...`: proxy ComfyUI `/view` with filename/subfolder/type validated against the job's own outputs. Stream video with HTTP Range support so playback can seek.
3. Progress: poll `/api/jobs/{id}` every 1 to 2 seconds, or bridge ComfyUI's websocket (`/ws?clientId=`) to the phone for step and percent progress.
4. Gallery: the last N jobs with thumbnails, full-screen view, save/share, and "run again" with the same params.

## Phase 5: subgraph expansion (unlocks groups C and D)

1. Read `definitions.subgraphs`, inline each instance, and namespace the inner node IDs as `<outer>:<inner>` (the API format ComfyUI itself uses).
2. Rewire the subgraph's input and output slots to the outer links. Apply widget values from the instance (these subgraph nodes carry their own `widgets_values` / proxy widgets, e.g. 15 values for 9 widget inputs, so inspect the format against real files first).
3. Apply Phase 1's mode handling inside subgraphs as well.
4. Fail closed on nested subgraphs or slot shapes we don't support (none in the bundled set today).
5. Video: `SaveVideo` output goes through the Phase 4 Range-enabled viewer.

## Phase 6: phone UI rebuild

The current UI is a single minified 8-line `index.html`. Split it into readable HTML/CSS/JS files (still static, no build step).

- Workflow list grouped by category (text→image, image→image, edit, video), with a runnable/not-runnable badge and the reason, from the Phase 0 checks.
- A form built from the Phase 2 parameters.
- Live progress, then the result inline. The gallery tab comes from Phase 4.
- A sync screen: last run, files added or changed, and conversion status per synced workflow.

## Phase 7: sync hardening (see `workflow-sync-plan.md`)

Periodic polling, recording remote deletions as archived instead of deleting, keeping revision history, and re-running conversion diagnostics after each sync.

## Feature ideas (after the core works)

| Feature | Value | Effort |
| --- | --- | --- |
| ✅ Cancel/interrupt a job, remove queued jobs (`/interrupt`, `/queue` delete) | High | S |
| ✅ Saved prompts per workflow (on the device for now; NAS-side presets later) | High | S |
| ✅ PWA share target: share a photo from the gallery straight into an image-to-video or edit workflow | High | M |
| ✅ Notification when a job finishes while the app is backgrounded (true push when the app is closed still needs Web Push + VAPID) | High | M |
| ✅ Model and LoRA picker: swap the checkpoint/UNet, change or turn off the workflow's LoRAs, add up to 4 more (auto-detected, no `📱` titles needed) | Medium | S |
| ✅ Sampler settings (steps, CFG, sampler, scheduler, hi-res denoise) and output shape (1:1 … 16:9), ported from the Open WebUI ComfyUI Studio function | Medium | S |
| Caption options for auto-caption workflows (type, length, content mode, lighting/camera toggles) and named presets, also from ComfyUI Studio | Medium | M |
| Show the auto-caption text produced by the captioning workflows, edit it, then run again | Medium | M |
| ✅ PC status: online, VRAM in use, "free VRAM" button | Medium | S |
| Wake-on-LAN for the PC from the NAS | Medium | S |
| ✅ Batch: 1–8 runs with random or consecutive seeds; Run again / Same seed on every result | Medium | S |
| Before/after compare slider for image-to-image and edit | Low | S |
| Job history stored on the NAS (ComfyUI history is lost when it restarts) | Medium | M |

## Suggested milestones

1. **First image on the phone:** Phases 0, 1, 2 (prompt only), 4. Group A runs end to end.
2. **Photo in:** Phase 3 and the image parameters. Group B runs.
3. **Subgraphs:** Phase 5. Groups C and D run, video included.
4. **Feels like an app:** Phase 6 plus the top feature ideas.

Housekeeping: update README (it still says UI workflows can't run). Bump the version in both places it appears with each milestone.
