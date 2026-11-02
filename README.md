# Comfy Mobile — v0.7.1

Mobile-first ComfyUI dashboard running on a NAS, connecting to ComfyUI on another machine.

## Docker image

GitHub Actions builds and publishes `ghcr.io/rpeters1430/comfy-mobile:latest` on pushes to `main`. Version tags `v*` are also published. Bundled and synced LiteGraph UI-format workflows are converted automatically to ComfyUI API format upon execution.

## Features

- **Mobile-first PWA**: Standalone installable app with responsive design, instant workflow search, and Web Share Target (share photos directly from your phone's gallery into workflows).
- **Automated Workflow Conversion**: Inlines subgraphs, repairs links, maps dynamic combos/autogrow inputs, and resolves bypassed/reroute nodes without needing desktop API exports.
- **Run-time Controls**: Swap checkpoints or UNets, adjust or turn off LoRAs, dynamically chain up to 4 extra LoRAs, tweak sampler settings (steps, CFG, sampler, scheduler, denoise), change output aspect ratios, and configure VLM auto-captioning parameters.
- **Batch Runs & Progress**: Queue 1–8 runs with random or sequential seeds, track live progress with queue indicators, interrupt/cancel running jobs, and stream Range-enabled video and WebP previews.
- **Phone wallpapers**: Pixel (including Pixel 10/Pro/Pro XL), iPhone and Galaxy presets, rendered near the model's native pixel budget and fit to the exact screen for native presets. Standard tall presets keep conservative 9:16 render-and-crop behavior; Pixel Cinematic-ready renders close to the final portrait aspect instead of trimming a 9:16 image, and adds a configurable 0–20% prompt-based subject safety margin (12% default). This is composition guidance, not a guarantee against Pixel's on-device zoom. Normal wallpaper mode is unchanged.
- **Notifications and gallery**: The NAS watches each job and sends a push notification when it finishes, even with the app closed (needs HTTPS). Finished jobs and thumbnails are kept on the NAS, so the gallery survives ComfyUI restarts.
- **Workflow Sync**: Safe, one-way pull of workflows directly from ComfyUI's user-data directory to your NAS.

## Pixel Cinematic-safe export

The image result viewer includes **Pixel wallpaper** for PNG/JPEG/WebP files. It builds a second, exact-device-resolution PNG with configurable 0–20% framing padding and a feather-blended background, without modifying the original output. A yellow guide previews a user-adjustable *approximate* crop (Google's actual cinematic zoom is device-controlled and may differ). All processing is local to the phone browser; no image is uploaded to an external service. Pixel 10/10 Pro/10 Pro XL and legacy 1080×2400 presets are available. Large PNG exports can use phone memory; on older phones, use the original file if export fails.\n\n## New in v0.7.1

Fixes from a code audit. Workflows greyed out for a missing model or node become runnable within about a minute of fixing it on the PC, with no container restart. Presets saved by older releases report as invalid instead of failing. Live progress stays visible for large batches. All app scripts are revalidated on load, so a phone never mixes two releases after an update. Gallery images reuse one connection to ComfyUI. Direct Python dependencies are now pinned.

## New in v0.7.0

**AI settings review now calls your model directly**, even when there are no verified web pages. It reviews the effective model/sampler settings, returns an assessment, and offers validated optional trial changes labelled as AI suggestions. Published recommendations remain a separate evidence category. Missing or failed AI connections cannot report a successful AI review.

Expand **AI connection setup** to choose Ollama/OpenAI/Gemini/OpenRouter, load models, and **Test & save connection**. Keys are stored on the NAS and never returned to the screen. No Docker environment edits are required for AI setup. Web research is now optional and off by default; SearXNG is needed only if you enable it. See [setup and limitations](docs/adviser.md).

## New in v0.6.1

Model research now handles Windows model paths when matching Civitai files, searches with readable model names, filters unrelated results, and keeps wrong-version pages as brief diagnostics. Markdown settings tables and Diffusers inference keys are parsed explicitly. A single identified base sampler may be reviewed when followed by connected refinement passes; those refinement settings remain unchanged. Workflow diagnostics include the detected model and sampler counts.

## New in v0.6.0

Optional **Research & review settings** reads published model settings through your SearXNG instance and model-card adapters. It preserves tables and split-line settings, shows extraction/AI diagnostics and source quotes, and proposes validated changes for an unambiguous base generation pass. Apply selected changes to the form, then press Generate. Reviews capture batch seeds and are checked again against the current workflow before submission.

Use local Ollama or explicitly configure an OpenAI, Gemini, OpenRouter or compatible structured-output endpoint. Keys stay in NAS environment variables. No automatic paid fallback; normal generation works without research. See [configuration, provider examples and limitations](docs/adviser.md).

## New in v0.5.1

Tap a Gallery photo to open its full-size image in a full-screen detail screen, keeping your Gallery position when you close it. Expand **Photo details** for workflow, creation time, seed, filename, resolution, prompt, recorded negative prompt, model/LoRA/sampler settings and job ID. Save/share are on this screen; jobs with multiple outputs have Previous/Next controls. Comparison with the first input is optional, so the viewer initially shows the generated image unobscured. Video outputs keep playback controls.

New jobs record effective model and sampler values as a small metadata snapshot. Older jobs show the settings available in their saved request and mark missing information as **Not recorded**. Full originals still live on the ComfyUI PC. If an original is unavailable, the viewer tries the saved NAS preview and labels it as a preview.

A broken synced workflow no longer hides a working bundled copy with the same name. The bundled copy is labelled as a fallback; the synced copy remains in Unavailable with its original error. This does not rewrite the ComfyUI workflow or silently guess missing links/models. Repair/re-save that workflow in ComfyUI and sync again when ready.

## New in v0.5.0

- **Shared gallery:** loads NAS job history on every device, with pagination. New records retain complete rerun settings; older device records still work. “Clear device list” only clears local history. NAS records retain the most recent 500 jobs.
- **Shared named presets:** expand **Shared presets** in a workflow. Save a name, then load it from any device. Presets include models, LoRAs, sampling, caption, prompt, seed and wallpaper choices. They exclude input photos. Loading checks the current workflow/model options first. Saving with the selected name updates that preset; a new name creates another.
- **Review captions:** for compatible Qwen/JoyCaption workflows, select **Review caption before rendering**, upload a photo and generate. The first job only captions the image. Edit positive/negative text in its result card, choose a run count and generate the approved render. PreviewAny must be installed. Ambiguous workflows or caption-dimension consumers show an unsupported explanation.
- **Wake PC:** configure the NAS as described below, then use **Wake PC** in Settings. The app waits up to two minutes for ComfyUI. A wake packet does not start ComfyUI automatically; configure the PC's startup separately.
- **Compare and zoom:** image results offer **Compare / full screen** (or **Full screen & zoom** for text-to-image). Drag the before/after slider and zoom up to 3×. Multi-image workflows compare the first input. Inputs use WebP previews; full output files remain on the ComfyUI PC and need it online.

Presets use `data/presets.sqlite3`. Jobs and thumbnails retain the existing `data/jobs.json` and `data/thumbs` paths; input previews are saved in `data/inputs`. Keep the entire data folder when updating. All devices with the same studio token share the same history/presets.

## Wake-on-LAN configuration

Optional `.env` entries (use the PC's real MAC and your LAN's broadcast address):

```dotenv
WOL_MAC=AA:BB:CC:DD:EE:FF
WOL_BROADCAST=192.168.8.255
```

The example broadcast is for `192.168.8.0/24`. Configure Wake-on-LAN on the PC's network adapter and firmware. The NAS must be able to send UDP broadcast to the PC's LAN; Tailscale alone does not forward LAN broadcasts. Wake requests are authenticated and limited to one every 30 seconds. The phone cannot select a destination.

Docker bridge networking may block LAN broadcast. On a Linux NAS, if necessary use `network_mode: host` on the service and remove its `ports` mapping. Preserve the current `COMFYUI_URL`, `.env`, volumes and data. The app continues to listen on port 8095. Check your NAS firewall/network configuration before changing networking.

## Reviewed-caption LoRA suggestions

For Image → Prompt → Image workflows, select **Review caption before rendering**, generate the caption, and open the resulting caption editor. Tap **Suggest LoRAs for this caption** to check installed, architecture-compatible LoRAs against the edited positive caption. Matches use distinguishing local training tags and ModelHarbor's verified training family, activation words, and example-derived strengths when available. Suggested LoRAs start **unchecked**: choose them, adjust their 0–2 strengths and optional activation word, then generate. Changing the caption clears stale suggestions; no model is downloaded or replaced.

The older **Auto-add matching LoRAs** switch remains available for users who want that convenience. Explicit caption-editor selections override automatic matches for that render. Cross-variant SDXL LoRAs still require judgment: Pony, Illustrious, and standard SDXL may load but behave differently. No suggestion guarantees a particular image result.

## LoRA trigger words and strengths (optional)

If you run ModelHarbor, point the app at it and LoRAs get their Civitai trigger words, a recommended strength and their base model:

```dotenv
MODELHARBOR_URL=http://192.168.8.137:8099
MODELHARBOR_TOKEN=<ModelHarbor's MODELHARBOR_AGENT_TOKEN>
```

LoRA pickers group LoRAs by the selected checkpoint's broad model family and show compatibility guidance. Civitai-verified ModelHarbor training-base metadata takes priority over filename guesses; **Pony, Illustrious, and NoobAI LoRAs may all use SDXL architecture but still behave differently**, so the picker shows a non-blocking notice for verified cross-variant pairings. Unknown compatibility is shown as unverified rather than guaranteeing success. A LoRA with one trigger word has it added to your prompt when you generate; one with several shows them as chips to tap into the prompt. Picking a LoRA sets the strength you last used it at, else the median strength seen in available Civitai examples (which is a suggestion, not a guarantee). Without ModelHarbor, a trigger word guessed from the LoRA's training tags is used where there is one.

## UGREEN NAS

Keep `.env` on the NAS (never commit it). Set `COMFYUI_URL` and `STUDIO_TOKEN`. Create the data folder once (the container runs as uid 1000), then run:

```sh
cd /volume1/docker/comfy
mkdir -p data && sudo chown 1000:1000 data
sudo docker compose pull
sudo docker compose up -d
```

The included `compose.yaml` uses the GHCR image. Preserve your existing `.env` and workflow files when updating. The token is stored in the browser after successful verification and can be forgotten via the UI.

## Security

Keep the service on a trusted LAN or Tailscale network, not exposed to the public internet. Avoid putting secrets in GitHub. The bundled workflow files are examples and may require custom nodes/models to run after API conversion.

## Development verification

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q app
node --test 'tests/*.test.js'
```

`python -m pytest -q` (install pytest separately) also collects the bundled workflow smoke test.
Optional browser regression coverage: install Playwright in your development environment,
run `npx playwright install chromium`, then `node tests/browser-mobile.cjs`.
The browser suite mocks ComfyUI and network actions; a real PC wake and custom-node caption
run still require integration testing on the NAS/PC.
