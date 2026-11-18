# Roadmap

Status as of 2026-11-17. Released version: 0.8.0 (2026-11-06). Sync details are in `workflow-sync-plan.md`.

## Where we are

The original plan (phases 0–7: converter, parameters, image inputs, outputs/gallery, subgraphs, UI rebuild, sync) is done. Every workflow saved on the PC runs from the phone unless a model or node is missing there.

| Area | State |
| --- | --- |
| Sync | One-way pull of PC workflows (`/userdata`); the catalog shows synced PC workflows only. Reset and Sync now work while the PC is off |
| UI → API conversion | Subgraphs, bypass/mute, V3 COMBO / dynamic combo / autogrow, LoRA Manager widgets, link repair, cross-OS model paths. Frontend-only widgets need a `FRONTEND_WIDGETS` entry (Power Puter so far) |
| PC workflows (live check 2026-11-17) | 4/6 runnable: SDXL, SD15, MOSS Image to Prompt to Image and RenPy Anime Character Gen. Krea2 fails because its UNET (`finepornV5TurboNSFW_v5_int8_convrot`) is not on the PC. `Untitled 2026-11-14 16-20-09` is empty |
| Bundled `workflows/` | 33 files, kept for tests only (not in the catalog); 1/33 converts against the current PC |
| Run-time controls | Model/UNet, LoRAs (+4 extra), sampler, size/aspect, video length/quality, caption options, all node settings, negative prompt, wallpaper fit |
| Jobs | Batches 1–8, wildcards and snippets, live progress stream, shared queue with Run next, held jobs while the PC is off, Wake-on-LAN, push notifications |
| Gallery | NAS-stored history (SQLite), search/filter/favorites/multi-select, full-screen viewer, compare slider, use as input, photo details |
| AI | Settings adviser (Ollama/Lemonade/OpenAI/Gemini/OpenRouter), optional web research, image vision review with "Run again with fixes", LoRA suggestions and trigger words (LoRA Manager / ModelHarbor) |
| Other | Shared presets, LoRA Manager recipes, caption review before render, anime character naming in captions, share target, home-screen shortcuts |
| Tests | 265 Python tests, 11 JS test files; CI runs them and builds the image on every push |

## Unreleased (on `main` since 0.8.0)

Not yet in a version bump or README "New in" section:

- PC workflow changes reach the phone: every node setting, LoRA Manager LoRAs, update notices
- Caption switches kept per workflow; anime/game character names in auto captions
- LoRA Manager recipes: sync and generate from a recipe (phone UI not yet checked in a real browser)
- LoRA Manager trigger words and strengths; adviser works with LoRA Manager / PAG workflows
- AI review of finished images, AI review button on result cards, "Run again with fixes"
- Fixes: Generate doing nothing (step limits), AI review apply (negative prompt, phone-added LoRA strengths), Civitai multi-version descriptions, Power Puter `code` dropped in conversion

## Known issues

- **Silently skipped widgets.** A node whose widgets come only from its frontend script (not `/object_info`) loses those values in conversion, and the job fails only when ComfyUI reaches that node. Power Puter did this after 3½ minutes. Fix: fail closed when `widgets_values` has values left over that no input took.
- **Krea2 workflow** needs its UNET on the PC, or the workflow re-saved with an installed one.
- **Bundled workflows** are stale (1/33 runnable) and only used by tests. Decide whether to drop them and the 33-file assertion.
- **Recipes UI** has not been checked on a phone; run `tests/browser-mobile.cjs` against it.

## Next

| # | Feature | Notes | Effort |
| --- | --- | --- | --- |
| 1 | Fail closed on leftover widget values | See known issues; refuses the workflow up front with the node name | S |
| 2 | One-tap Upscale on results | Reuse the wallpaper `_apply_fit` upscale nodes; 4x-UltraSharp and 4x-AnimeSharp are on the PC. Optional hires refine pass | S–M |
| 3 | Prompt enhance / describe image | ✨ Enhance next to the prompt box and Describe on photos, through the adviser connection (text model for enhance, vision model for describe) | M |
| 4 | Fast / Balanced / Best quality chips | Per model family steps/CFG/hires presets on top of the existing sampler controls | S |
| 5 | Release 0.9.0 | Bump `VERSION` and README, with a "New in v0.9.0" section from the unreleased list | S |

## Later

| Feature | Notes |
| --- | --- |
| Mask editor for inpainting | Only worth it once an inpaint workflow is on the PC; none today |
| One-tap Expand (outpaint) on results | Same dependency as the mask editor |
| Sync screen | Last sync, files added/changed, conversion status per workflow (catalog already shows errors per card) |
| Sync history | Keep revisions and archive deletions instead of removing them |
