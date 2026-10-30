# Comfy Mobile — v0.4.0

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
