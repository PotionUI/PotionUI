# HuggingFace Provider Plugin

A marketplace provider plugin for PotionUI that integrates with the HuggingFace Hub.

## Features

- **Model Search**: Search for models on the HuggingFace Hub
- **Download URLs**: Resolve a repo (and optionally a specific file/revision) to a
  download URL, including gated and private repos
- **Authenticated Downloads**: Bearer-token auth for gated/private repos; public repos
  download anonymously without any configuration

## Installation

1. Go to Admin -> Plugins
2. Enable the "HuggingFace Provider" plugin
3. (Optional) Configure your HuggingFace access token for gated/private repos

## Configuration

### Access Token (Optional)

Public repos download without any token. Configure a token only if you need:
- Access to gated repos (e.g. models requiring a license acceptance click-through)
- Access to private repos

Get a token from https://huggingface.co/settings/tokens.

### Rate Limit Delay

Configure the delay between API requests to avoid hitting rate limits. Default is 0.5
seconds.

## Ref shape

Unlike CivitAI (where a "model version" already identifies one file), a HuggingFace repo
contains many files. `get_download_url(provider_model_id, provider_version_id)` uses:

- `provider_model_id` = `"{org}/{repo}"`, e.g. `"black-forest-labs/FLUX.1-dev"`
- `provider_version_id` = `"{revision}@{filepath}"`, e.g. `"main@flux1-dev.safetensors"`

A HuggingFace repo has no single "primary" file, so `provider_version_id` is required -
an omitted one raises `ProviderNotFoundError` naming the repo rather than guessing a file.

## Not supported

HuggingFace has no hash-reverse-index API (unlike CivitAI's
`/model-versions/by-hash/{sha256}`), so `HASH_LOOKUP` is not advertised and
`get_model_by_hash` always returns `None`.

## API Capabilities

| Capability | Supported |
|------------|-----------|
| Hash Lookup | No (not offered by the HuggingFace API) |
| Search | Yes |
| Download URL | Yes |
| Model Info | Yes (via repo file listing) |
| Media Download | No |

## Troubleshooting

### 401 / "requires authentication" errors
- The repo is gated or private; configure an access token with access to it

### "Connection timeout" errors
- Check your internet connection; huggingface.co may be experiencing high traffic
