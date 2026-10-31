# Model research and settings review

Open a workflow on Create, select models/LoRAs and add inputs, then expand **Research & review settings**. Research does not run a ComfyUI generation. It returns cached source cards, extraction diagnostics, and changes supported by quoted model-specific evidence. Select changes, choose **Apply selected to settings**, then press **Generate** explicitly.

For a caption preview, edit the caption and choose **Review settings with this caption** to research the effective render before using the main Generate button. The existing direct render button remains available.

Random/sequential batch seeds are captured at review time. Changing the workflow, prompt, images, model options, settings or seeds invalidates its approval association. The server checks the rebuilt graph again before generation. Manual generation remains available without the adviser. New reviewed jobs retain source quotes and selected changes in gallery details. Presets and reruns retain settings, not expiring approval tokens.

## Build availability

This feature is prepared on a branch. The published image gains it only after merge and a successful image build. The repository compose file pins an image digest: update that pin to the newly built digest (or deliberately select a published version) before pulling. Keeping the old digest keeps the old application even if the tag says latest. For local verification, build the Dockerfile from this feature checkout.

## NAS configuration

Preserve your existing `.env`, token and data volume. Add the variables below to `.env`; the supplied compose file passes them into the container. Never commit an API key or paste one into a workflow/prompt.

Local Ollama:

```dotenv
SEARXNG_URL=https://searxng.tailb94d9.ts.net
ADVISER_PROVIDER=ollama
OLLAMA_URL=http://192.168.8.143:11434
ADVISER_MODEL=YOUR_INSTALLED_MODEL_NAME
```

The names above use your supplied function's URL defaults. They are not a connectivity test. Docker must resolve/reach that SearXNG address; a reachable LAN URL or shared Docker network hostname also works. Enable `json` under SearXNG `search.formats`. Choose an installed instruction model; the app never downloads or silently selects a model. The adviser can extract explicit numeric settings without AI, but AI assists named options and complex passages.

For hosted AI, select exactly one provider:

| ADVISER_PROVIDER | Default API base | ADVISER_MODEL |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | Your accessible model supporting structured output |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | Your accessible Gemini model supporting compatible structured output |
| `openrouter` | `https://openrouter.ai/api/v1` | An exact model ID supporting structured output |
| `compatible` | Set `ADVISER_BASE_URL` | A compatible model ID |

```dotenv
ADVISER_PROVIDER=gemini
ADVISER_MODEL=YOUR_CHOSEN_MODEL_ID
ADVISER_API_KEY=YOUR_PROVIDER_API_KEY
```

`ADVISER_BASE_URL` optionally overrides a provider's base. Use the API base, **without** `/chat/completions`. Hosted providers require HTTPS. Credentials are server-side environment variables, never returned to the browser. There is no automatic provider switch, paid retry, or cloud fallback: change the configured provider yourself. The chosen model/endpoint must accept `response_format: json_schema`; an unsupported endpoint returns a diagnostic instead of silently downgrading.

Hosted requests send model filenames and bounded source excerpts, not your images, prompts or full workflow. Search engines receive technical queries containing model filenames. Costs and provider data handling depend on your selected service. One AI call per uncached model, at most 16 targets per review, one review executing at a time, at most two waiting. Ordinary cache hits make no AI call. Maximum output per call is bounded; incomplete output is identified and rejected. Keep the service on LAN/Tailscale.

## Reading failures

Expand a model card for search count, source fetch/extraction result, extracted settings text, identity match, AI validation outcome and rejected candidate reasons.

- **SearXNG JSON disabled:** allow JSON output; HTML search success is insufficient.
- **Source unavailable/blocked/too large:** search found a link but it could not be read. No settings are inferred from its search snippet.
- **Unverified source:** the extracted text did not establish the exact model/version. Brief source links remain visible; mismatched page excerpts are excluded from AI input and unsupported changes are withheld.
- **Invalid/incomplete settings JSON:** provider parsing failed or output hit its limit; explicit numeric extraction can still succeed.
- **Conflicting evidence:** incompatible source settings are displayed without choosing an arbitrary winner.
- **No justified changes:** distinguish already-in-range settings from missing evidence using the cards; this is not a certification that the whole workflow is optimal.

Refresh saved research after changing providers or when testing parsing fixes. Successful settings cache for 30 days; empty/error results for one hour. Research and approvals live in `/data/adviser.sqlite3`; pending reviews and approvals expire after 10 minutes. Interrupted research is marked on restart. Source retrieval and AI errors do not block normal generation.

## First-release limits

Public adapters support Civitai exact file/version matches, Hugging Face model cards, GitHub/raw GitHub documents, and ComfyUI documentation. Other domains are explicitly reported as unsupported; this is not a general web browser. Source fetches are bounded, public HTTPS only, with validated pinned DNS and redirect checks. Civitai APIs may still require access the app does not have; no authentication-bypass service is used.

Changes are limited to steps, CFG, sampler, scheduler and paired latent size on an unambiguous base generation pass. One identifiable base pass can be reviewed with connected downstream refinement passes; refinement settings remain unchanged. Parallel branches, multi-model/video splits, acceleration LoRA combinations and unknown wrappers can show research but withhold executable suggestions. Model replacement, LoRA strength, prompts and denoise remain manual. Final wallpaper fit is preserved.

Trafilatura and explicit table preservation replace basic HTML stripping. Schema-constrained AI assists evidence extraction; deterministic validation selects changes. Neither finding a page nor producing valid JSON establishes that its settings apply to your model. Browser rendering/Crawl4AI and benchmark comparisons are follow-ups, not included in this release.

Automated verification uses mock services and extracted-page fixtures. Your real NAS services, chosen provider/model and a previously failing model page still need a live test after deployment. No NAS/container update is performed by preparing this branch.
