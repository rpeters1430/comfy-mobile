# Comfy Mobile

Mobile-first ComfyUI dashboard, running on a NAS and connecting to a separate ComfyUI instance.

## Status
Repository initialized with GitHub Actions for Docker builds. Application source must be added before the first image can build.

## Image
After application source is committed to `main` and the workflow succeeds:

```bash
docker pull ghcr.io/rpeters1430/comfy-mobile:latest
```

GitHub Actions also publishes immutable `sha-...` tags and `v*` release tags.

## Security
Keep `.env` and `STUDIO_TOKEN` on the NAS, never in Git. The workflow uses GitHub's built-in `GITHUB_TOKEN` to publish packages. Package visibility may need to be changed to public under GitHub Packages before anonymous NAS pulls work.

## Deployment
The app is expected to listen on port 8095. Preserve the NAS's existing `.env` and workflow files when switching from local builds to the registry image.
