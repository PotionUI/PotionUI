# Providers

A **provider** is a plugin that knows how to talk to one model marketplace: look a model
up by hash, search it, fetch its metadata and preview media, and authenticate a download.

Providers are the marketplace counterpart of [engines](backends.md), and follow the same
rule: **core never names one.** `civitai` is contributed by the `civitai-provider`
marketplace plugin and may be absent entirely. There is no built-in provider — if you
don't want a marketplace, don't install one.

## A provider owns its credentials

This is the whole point of the abstraction. A provider declares its own settings in its
plugin manifest, and they are edited in **Admin → Plugins**:

```yaml
# content/plugins/marketplace/civitai-provider/manifest.yml
settings:
  - name: "api_key"
    type: "string"
    format: "password"
    label: "CivitAI API Key"
    required: false
```

`MarketplaceProviderService` passes those settings to `provider.initialize(settings)` at
discovery time, and the provider keeps them. Downloads authenticate through
`provider.prepare_download()` (default: merge `get_download_headers()` and return
`get_authenticated_download_url()`) — see `src/features/downloads/worker.py`, which looks
the provider up by `download.provider_id`, or by asking each provider
`matches_download_url(url)` when no provider was named.

There is **no global API key setting**. `civitai_api_key`, `hf_api_key` and `ca_api_key`
were removed by migration `071`: a second copy of a credential is a second copy that can
disagree with the first. `ca_api_key` never had a reader at all.

> Upgrading: migration `071` carries an existing `civitai_api_key` over to the
> `civitai-provider` plugin's `api_key` setting, so nothing is lost.

### HuggingFace

There is no HuggingFace provider. Public files download without credentials. A gated-model
provider would be a plugin like any other, owning its own token. Do not reintroduce a core
`hf_api_key` setting to work around its absence.

## Presets never download models

`models.yml` is gone, along with the `downloader` and `to_iotype` pipes, the `ModelProvider`
enum, `ModelTemplate` and the `MODEL_TEMPLATE` IO type. A preset declares the pipeline it
runs, not the files it needs. Models are downloaded on demand through the core download
queue (`src/features/downloads/`), which resolves credentials through the provider registry.

`ModelDirectories` (`src/features/models/directory.py`) now owns only the models-directory layout. It does not
download.

## Writing a provider

Everything a provider imports comes from `src.plugin_api` — see [the Plugin API](plugin-api.md).

Subclass `MarketplaceProviderBase` and register it from the `provider.register` hook:

```python
# hooks/provider_hooks.py
from src.plugin_api import HookContext

def register_provider(context: HookContext) -> HookContext:
    from ..provider.my_provider import MyProvider
    context.data["providers"]["my-marketplace"] = MyProvider
    return context
```

```yaml
# manifest.yml
hooks:
  backend:
    - hook: "provider.register"
      handler: "hooks.provider_hooks.register_provider"
```

Declare what you support via `ProviderCapability`: `HASH_LOOKUP`, `SEARCH`, `DOWNLOAD_URL`,
`MODEL_INFO`, `MEDIA_DOWNLOAD`, `API_KEY_REQUIRED`.

The methods that matter: `initialize(settings)`, `get_model_by_hash(sha256)`,
`search_models(...)`, `get_download_url(...)`, `get_download_headers()`,
`test_connection()`, and `get_settings_schema()`.

## Where providers are used

- `operations.fetch_provider_info` / `run_provider_fetch`
  (`src/features/models/operations.py`, dispatching onto `ProviderInfoFetcher`) — fetch and
  store model metadata. It raises if the named provider is not registered or not
  initialized, telling the admin to install and configure the plugin.
- The core download queue's worker (`src/features/downloads/worker.py`) — authenticated downloads.
- The automation action `action.fetch_provider_metadata`, which takes a `provider` name in
  its config. It replaced `action.fetch_civitai_metadata`, which hardcoded one marketplace;
  migration `071` retypes existing automations.
- A `model` field's `recommendations:` (`docs/presets.md`) may reference a provider by name
  (`{name, provider, ref}`). At form-schema serialization time
  (`src/features/fields/model.py:_serialize_recommendations`) any recommendation naming a provider
  that isn't installed/enabled is silently dropped — the frontend never sees it, and never
  gets to offer a download button that would fail. Provider-less recommendations
  (`{name, link}`) always pass through.

## Troubleshooting

**"Provider 'x' not found"** — the plugin providing it is not installed or not enabled.

**"Provider 'x' is not initialized"** — the plugin is enabled but its settings (usually the
API key) have not been configured. Admin → Plugins.

**Downloads fail with 401 on a gated model** — the provider has no API key configured, or
the marketplace requires one it doesn't declare.
