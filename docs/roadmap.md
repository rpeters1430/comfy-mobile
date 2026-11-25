# Roadmap

Status as of 2026-11-17. Released version: 0.9.0 (2026-11-17). Sync details are in `workflow-sync-plan.md`.

## Where we are

The original plan (phases 0–7: converter, parameters, image inputs, outputs/gallery, subgraphs, UI rebuild, sync) is done. Every workflow saved on the PC runs from the phone unless a model or node is missing there.

| Area | State |
| --- | --- |
| Sync | One-way pull of PC workflows (`/userdata`); the catalog shows synced PC workflows only. Reset and Sync now work while the PC is off |
| UI → API conversion | Subgraphs, bypass/mute, V3 COMBO / dynamic combo / autogrow, LoRA Manager widgets, link repair, cross-OS model paths. Frontend-only widgets are mapped for Power Puter, Power Lora Loader and TriggerWord Toggle; any other unmapped saved value fails the workflow up front |
| PC workflows (live check 2026-11-25) | 2/2 runnable and run end to end through the app: SDXL and MOSS Image to Prompt to Image (Ollama). Two `.json.bak` backups in the folder are ignored by sync |
| Bundled `workflows/` | 33 files, kept for tests only (not in the catalog); 1/33 converts against the current PC |
| Run-time controls | Model/UNet, LoRAs (+4 extra), sampler, size/aspect, video length/quality, caption options, all node settings, negative prompt, wallpaper fit |
| Jobs | Batches 1–8, wildcards and snippets, live progress stream, shared queue with Run next, held jobs while the PC is off, Wake-on-LAN, push notifications |
| Gallery | NAS-stored history (SQLite), search/filter/favorites/multi-select, full-screen viewer, compare slider, use as input, photo details |
| AI | Settings adviser (Ollama/Lemonade/OpenAI/Gemini/OpenRouter), optional web research, image vision review with "Run again with fixes", LoRA suggestions and trigger words (LoRA Manager / ModelHarbor) |
| Other | Shared presets, LoRA Manager recipes, caption review before render, anime character naming in captions, share target, home-screen shortcuts |
| Tests | 281 Python tests, 11 JS test files (54 tests); CI runs them and builds the image on every push |

## Unreleased

Nothing since 0.9.0. Release notes are in the README.

## Known issues

- **Krea2 workflow** needs its UNET on the PC, or the workflow re-saved with an installed one.
- **Bundled workflows** are stale (0/33 runnable on the ComfyUI install checked 2026-11-25: missing custom nodes and models) and only used by tests. With the PC on, run `curl -o fixtures/object_info.local.json http://<comfy>:8188/object_info && python tools/check_workflows.py --trim`, then drop or re-save the workflows that fail and commit `fixtures/object_info.bundled.json`. CI then converts every bundled workflow on each push.
- **Recipes UI** has not been checked on a phone; run `tests/browser-mobile.cjs` against it.

## Next

| # | Feature | Notes | Effort |
| --- | --- | --- | --- |
| ✅ 1 | Fail closed on leftover widget values | Unmapped saved values now fail the workflow up front with the node name, instead of mid-run (Power Puter did this after 3½ minutes). Upload buttons and ShowText display text are ignored | S |
| ✅ 2 | Upscale on results | ⤢ Upscale on result cards and the viewer: pick a model and 2×/4×; the saved image is read from ComfyUI's output folder, no re-upload. A hires refine (img2img) pass is not done yet | S–M |
| ✅ 3 | Prompt enhance / describe image | ✨ Enhance and 📷 Describe a photo under the prompt box, with Undo. Enhance uses the AI connection's text model, Describe its vision model; both write in the picked checkpoint's prompt style (tags, phrases or sentences) | M |
| 4 | Fast / Balanced / Best quality chips | Per model family steps/CFG/hires presets on top of the existing sampler controls | S |
| ✅ 5 | Release 0.9.0 | Released 2026-11-17 | S |

## Later

| Feature | Notes |
| --- | --- |
| Mask editor for inpainting | Only worth it once an inpaint workflow is on the PC; none today |
| One-tap Expand (outpaint) on results | Same dependency as the mask editor |
| Sync screen | Last sync, files added/changed, conversion status per workflow (catalog already shows errors per card) |
| Sync history | Keep revisions and archive deletions instead of removing them |
