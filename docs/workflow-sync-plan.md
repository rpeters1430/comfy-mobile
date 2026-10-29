# ComfyUI workflow synchronization plan

## Goal
Keep NAS workflows in sync with ComfyUI Desktop without overwriting user edits or requiring manual JSON exports.

## Discovery (phase 1)
The authenticated GET /api/comfy/workflows/discovery endpoint probes ComfyUI's user-data listing and returns JSON workflow filenames when available. It is read-only. Endpoint support and response shape must be verified on the user's ComfyUI Desktop version.

## Download and reconcile (phase 2)
- Probe supported user-data file download endpoints against actual ComfyUI Desktop behavior; do not assume that the workflow library is accessible over HTTP.
- If unavailable, offer an opt-in Windows-side sync agent reading the configured ComfyUI user/workflows directory and uploading to an authenticated NAS endpoint.
- Never accept arbitrary file paths, symlinks, or files over size limits.
- Store remote workflows in a separate writable `/workflow-sync` mount, not in the existing read-only `/workflows` mount.
- Build a merged catalog from bundled and synced workflows; synced entries get distinct source identifiers.
- SHA-256 compare and update only changed files; use atomic temp-file rename.
- Never delete local workflows automatically; record remote deletions as missing/archived.
- Maintain previous revisions and log sync operations.
- Poll periodically with bounded concurrency, conditional requests where supported, and a manual Sync Now action.
- Avoid exposing the ComfyUI user-data API directly to the browser.
- Explicitly require opt-in for automatic import; never sync sensitive workflow contents to GitHub.
- Validate UI/API format and subgraphs separately from file synchronization.

## Validation criteria
- New and updated workflows appear without container rebuild.
- Conflicts preserve local edits.
- Disconnected PC and malformed responses do not delete or corrupt NAS workflows.
- Restart does not lose synchronized workflows.
- A changed remote workflow triggers updated catalog diagnostics.
