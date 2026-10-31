# Model research and settings review

Open a workflow on Create, select models/LoRAs and add inputs, then expand **AI settings review**.

1. Open **AI connection setup** and choose local Ollama, OpenAI, Gemini, OpenRouter or another compatible endpoint.
2. Enter the API base URL for local/custom services. Hosted presets fill their default base URL. Enter a provider key when required.
3. Use **Load models**, then select/type a model ID. Choose **Test & save connection**. This makes a real structured-output call to that model and saves only on success.
4. Press **Review with AI**. This sends model names and effective technical controls to the selected model; it does not generate an image. Read the assessment and optional proposed trial changes, apply selected changes, then press Generate explicitly.

AI review does not need SearXNG or matching web pages. Optional **Add published guidance from web research** uses the configured SearXNG instance and model-card adapters alongside the direct AI review. It is off by default. AI suggestions are labelled as model judgments, not author-published facts; web-backed suggestions include literal evidence. Neither is a guarantee of better output. Compare results with a fixed seed.

The result records whether AI actually completed, which provider/model responded, its assessment and any applicable changes. Missing configuration, connection failures, refusals, malformed or truncated output are explicit failures. A model returning no changes still supplies its assessment. It cannot be called a successful AI review merely because search returned results.

For a caption preview, edit the caption and choose **Review settings with this caption** to review the effective render before using the main Generate button. The existing direct render button remains available.

Random/sequential batch seeds are captured at review time. Changing workflow, settings, prompt or input references invalidates its approval association. The server checks the rebuilt graph again before generation. Manual generation remains available without AI. Saved presets/reruns retain settings, not expiring approval tokens.

Connection settings saved in the app take precedence over environment variables. The key remains server-side in `/data/adviser.sqlite3` with owner-only file permissions. The browser receives a saved-key indicator; leaving the key blank preserves it only when the provider and destination are unchanged. Enter a new key when changing provider/host. Keep the data volume private and preserve it when updating. Every device sharing the studio token shares this configuration.

## Build availability

This feature is prepared on a branch. The published image gains it only after merge and a successful image build. The repository compose file pins an image digest: update that pin to the newly built digest (or deliberately select a published version) before pulling. Keeping the old digest keeps the old application even if the tag says latest. For local verification, build the Dockerfile from this feature checkout.

## Optional environment configuration (advanced)

Preserve your existing `.env`, token and data volume. Add the variables below to `.env`; the supplied compose file passes them into the container. Never commit an API key or paste one into a workflow/prompt.

Local Ollama:

```dotenv
SEARXNG_URL=https://searxng.tailb94d9.ts.net
ADVISER_PROVIDER=ollama
OLLAMA_URL=http://192.168.8.143:11434
ADVISER_MODEL=YOUR_INSTALLED_MODEL_NAME
```

The names above use your supplied function's URL defaults. They are not a connectivity test. Docker must resolve/reach that SearXNG address; a reachable LAN URL or shared Docker network hostname also works. Enable `json` under SearXNG `search.formats`. Choose an installed instruction model; the app never downloads or silently selects a model. The configured AI must complete a real workflow review. Explicit extraction of published numeric settings is optional supporting research, not a replacement for that AI call.

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

Hosted workflow review sends model filenames and effective technical controls, not images, prompts or full graphs. Optional extraction calls also send bounded public source excerpts. Search engines receive technical queries containing model filenames. Costs and provider data handling depend on your selected service. AI-only review makes one model call per review. Optional web research may make additional extraction calls for uncached verified model pages, up to 16 targets per review. One review executes at a time, at most two wait. Web cache hits avoid extraction calls; the actual workflow AI review still runs. Maximum output per call is bounded; incomplete output is identified and rejected. Keep the service on LAN/Tailscale.

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

AI trial changes are limited to steps, CFG, sampler and scheduler on one identifiable ordinary KSampler base pass; unsupported graphs still get an AI assessment but no automatic changes. Published recommendations can additionally propose paired latent size where validated. One identifiable base pass can be reviewed with connected downstream refinement passes; refinement settings remain unchanged. Parallel branches, multi-model/video splits, acceleration LoRA combinations and unknown wrappers can show research but withhold executable suggestions. Model replacement, LoRA strength, prompts and denoise remain manual. Final wallpaper fit is preserved.

Trafilatura and explicit table preservation replace basic HTML stripping. Schema-constrained AI performs a direct workflow review; all proposed trial values are validated against allowed controls. In optional research, AI also assists extraction with literal evidence checks. Neither finding a page nor producing valid JSON establishes that its settings apply to your model. Browser rendering/Crawl4AI and benchmark comparisons are follow-ups, not included in this release.

Automated verification uses mock services and extracted-page fixtures. Your real NAS services, chosen provider/model and a previously failing model page still need a live test after deployment. No NAS/container update is performed by preparing this branch.
