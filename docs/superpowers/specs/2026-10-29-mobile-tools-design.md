# Mobile workflow tools

Implement the five accepted proposals from docs/security-review.md on top of merged PR 33.
Keep FastAPI, static JS/CSS, Docker and the existing workflow converter. Preserve bundled workflows.

The NAS is the shared source of job history and named presets. Extend existing jobs.json records
without losing old records; retain full rerun parameters and input previews for new jobs.
Expose paginated authenticated history, and merge device history by job ID in the phone UI.
Clearing the local list must be labelled as local; server records remain available.
Named presets save workflow, prompt, seed and complete UI choices (models, LoRAs, sampling,
caption, aspect, wallpaper), without uploaded filenames or secrets. Saving, loading and deleting
are authenticated. Validate against live workflow options and report stale choices before use.

Caption review is optional: supported caption workflows can queue an ancestor-only caption graph,
without running diffusion/video samplers. Show positive and negative text for editing. Rendering
replaces the caption node's text outputs in the original workflow with the approved text and
prunes unused caption dependencies. Ambiguous caption nodes/non-text consumers fail closed.
Use existing PreviewAny output support, and show an explanation when unsupported.

Wake-on-LAN uses WOL_MAC and WOL_BROADCAST server settings, UDP port 9, a cooldown and an
authenticated POST. Never accept arbitrary destinations from clients. UI polls health after wake;
the NAS need not share a network with the phone, but must reach the PC's broadcast domain.

Comparison uses an authenticated job-bound input preview and signed output URL. Add an accessible
touch slider, full-screen dialog and zoom for image outputs; preserve video controls. Persist
input WebP previews so comparisons can still use the NAS copy after ComfyUI restarts.

All new routes require authentication. Preserve security regression tests. Test missing storage,
legacy records, pagination, offline PCs, stale presets, unsupported caption graphs and absent
input previews. Default to one process (as the existing Docker command does); serialize JSON
job mutations and use SQLite transactions for shared preset writes.
