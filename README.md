# Comfy Mobile — v0.5.0

Mobile-first ComfyUI dashboard running on a NAS, connecting to ComfyUI on another machine.

## Docker image

GitHub Actions builds and publishes `ghcr.io/rpeters1430/comfy-mobile:latest` on pushes to `main`. Version tags `v*` are also published. Bundled and synced LiteGraph UI-format workflows are converted automatically to ComfyUI API format upon execution.

## Features

- **Mobile-first PWA**: Standalone installable app with responsive design, instant workflow search, and Web Share Target (share photos directly from your phone's gallery into workflows).
- **Automated Workflow Conversion**: Inlines subgraphs, repairs links, maps dynamic combos/autogrow inputs, and resolves bypassed/reroute nodes without needing desktop API exports.
- **Run-time Controls**: Swap checkpoints or UNets, adjust or turn off LoRAs, dynamically chain up to 4 extra LoRAs, tweak sampler settings (steps, CFG, sampler, scheduler, denoise), change output aspect ratios, and configure VLM auto-captioning parameters.
- **Batch Runs & Progress**: Queue 1–8 runs with random or sequential seeds, track live progress with queue indicators, interrupt/cancel running jobs, and stream Range-enabled video and WebP previews.
- **Phone wallpapers**: Pixel, iPhone and Galaxy presets, rendered near the model's native size and then upscaled and center-cropped to the exact screen. Wallpaper style chips and a Cinematic-ready mode for Pixel.
- **Notifications and gallery**: The NAS watches each job and sends a push notification when it finishes, even with the app closed (needs HTTPS). Finished jobs and thumbnails are kept on the NAS, so the gallery survives ComfyUI restarts.
- **Workflow Sync**: Safe, one-way pull of workflows directly from ComfyUI's user-data directory to your NAS.

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
