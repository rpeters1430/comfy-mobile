# Comfy Mobile — v0.3.0

Mobile-first ComfyUI dashboard running on a NAS, connecting to ComfyUI on another machine.

## Docker image

GitHub Actions builds and publishes `ghcr.io/rpeters1430/comfy-mobile:latest` on pushes to `main`. Version tags `v*` are also published. The initial 30 bundled workflows are UI-format and are **not yet runnable** from this app; API workflow conversion is a future milestone.

## UGREEN NAS

Keep `.env` on the NAS (never commit it). Set `COMFYUI_URL` and `STUDIO_TOKEN`. Then run:

```sh
cd /volume1/docker/comfy
sudo docker compose pull
sudo docker compose up -d
```

The included `compose.yaml` uses the GHCR image. Preserve your existing `.env` and workflow files when updating. The token is stored in the browser after successful verification and can be forgotten via the UI.

## Security

Keep the service on a trusted LAN or Tailscale network, not exposed to the public internet. Avoid putting secrets in GitHub. The bundled workflow files are examples and may require custom nodes/models to run after API conversion.
