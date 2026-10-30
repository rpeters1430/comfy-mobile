# Security review and next features

Reviewed against main commit `105c92d`. These findings come from code review and
local regression tests; no requests were made to a live NAS or ComfyUI server.

## Five verified problems and fixes

| Problem | Impact / conditions | Fix |
| --- | --- | --- |
| Active content from generated outputs (high) | A workflow/custom node could report an HTML or SVG file, which the backend would sign and serve with its upstream MIME type. Opening that file could execute script under the app origin, where the studio token is stored. This requires malicious output or an unexpected upstream response. | Accept raster image/video filenames and safe relative output paths only; reject active/unknown upstream MIME types; add `nosniff`, a sandbox CSP and no-referrer headers to streamed media. Signed URLs also validate output paths. |
| Sync response size checked after buffering (medium) | A very large workflow response could exhaust the container's 256 MB allocation before the 2 MB check ran. The listing was unbounded too. | Stream both listing and workflow responses with a 2 MB limit; request identity encoding and reject compressed responses before decoding to prevent compression bombs. |
| Catalog bypassed workflow size validation (medium) | `/api/catalog` parsed every matching file directly, including oversized files the job loader would refuse. This could exhaust memory when a large file was placed in a mounted workflow directory. | Use the same containment and 2 MB checks as validation/job submission. |
| Predictable temporary files and loose permissions (medium) | A local process able to write the shared data folder could plant a `.partial` symlink and have the app overwrite another file writable by its UID. Private keys/prompts could also be written with permissive umask-derived permissions; concurrent writers shared the same temporary filename. | Use exclusive random temporary files with mode 0600 and atomic replace, shared by job/push persistence and workflow sync. This prevents temporary-file collisions; it does not serialize read-modify-write updates. |
| Push destination validation and redirects (medium) | Malformed IPv6/port syntax raised errors; credentials and unrelated Google hosts passed the vendor-wide allowlist. The push dependency followed HTTP redirects outside the validated destination. | Reject malformed/credential-bearing URLs, narrow Google hosts to FCM/legacy push, and disable redirects in the HTTP session passed to Web Push. |

## Five proposed features

These were proposals in the security patch. All five are implemented in the v0.5.0 follow-up; usage and integration requirements are documented in README.md.

| Priority | Feature | Implementation outline |
| --- | --- | --- |
| 1 | NAS-backed gallery across devices | Add an authenticated, paginated job-list route over `JobStore`; merge NAS history with device records so a new phone/browser can recover saved jobs. |
| 2 | Named presets shared across devices | Save workflow ID, model, LoRAs, sampling, caption, size and wallpaper settings as versioned NAS records; validate against the current workflow before loading. |
| 3 | Review and edit captions before generation | Separate captioning from rendering; show an editable caption step, then submit the approved text to a compatible render workflow. |
| 4 | Wake the ComfyUI PC | Add a server-configured MAC/broadcast destination and authenticated Wake-on-LAN action; poll health and show startup progress. |
| 5 | Before/after image comparison | Retain the input preview with the job and add a touch-friendly comparison slider for image edits, plus full-screen zoom. |

## Verification and boundaries

Regression coverage includes oversized chunked responses/listings, unsafe media and paths,
upstream HTML MIME rejection, legacy gallery records, compressed responses, valid 206 Range media, symlink clobbering, permissions,
malformed push subscriptions and redirect refusal. Existing conversion, job, progress,
control and frontend-helper tests remain part of the full verification.

The patch does not deploy changes or modify `.env`, tokens, workflows or NAS data.
Keep LAN/Tailscale deployment as documented. Live custom-node output MIME types and
real browser push deliveries need an integration check on the user's installation.
The baseline Starlette TestClient deprecation warning is unrelated to these fixes.
