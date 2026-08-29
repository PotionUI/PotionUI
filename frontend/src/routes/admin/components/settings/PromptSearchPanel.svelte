<script lang="ts">
	import { createModelFetchController, modelNameLookup } from './modelFetch.svelte';
	import ModelFetchRow from './ModelFetchRow.svelte';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	const fetch = createModelFetchController(['prompt_embedding'], modelNameLookup(settings));
</script>

<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
	<div class="px-6 py-3 border-b border-line">
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Prompt Search</h3>
	</div>

	<div class="px-6 divide-y divide-line">
		<div class="py-4 space-y-4">
			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="prompt-embedding-provider" class="block text-sm font-medium text-fg mb-1">
						Embedding provider
					</label>
					<p class="text-sm text-fg-muted">Backend used to embed saved prompts for semantic search</p>
				</div>
				<select
					id="prompt-embedding-provider"
					class="input w-48 flex-shrink-0"
					value={settings.prompt_embedding_provider || 'local'}
					onchange={(e) => onSettingChange('prompt_embedding_provider', e.currentTarget.value)}
				>
					<option value="local">Local</option>
					<option value="ollama">Ollama</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="prompt-embedding-model" class="block text-sm font-medium text-fg mb-1">
						Model
					</label>
					<p class="text-sm text-fg-muted">
						{settings.prompt_embedding_provider === 'ollama'
							? 'Ollama model name'
							: 'Hugging Face model id'}
					</p>
				</div>
				<input
					id="prompt-embedding-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.prompt_embedding_model || ''}
					oninput={(e) => onSettingChange('prompt_embedding_model', e.currentTarget.value)}
				/>
			</div>

			{#if settings.prompt_embedding_provider === 'ollama'}
				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-ollama-base-url" class="block text-sm font-medium text-fg mb-1">
							Ollama base URL
						</label>
						<p class="text-sm text-fg-muted">Base URL of the Ollama server</p>
					</div>
					<input
						id="prompt-embedding-ollama-base-url"
						type="text"
						class="input w-64 flex-shrink-0 font-mono text-sm"
						value={settings.prompt_embedding_ollama_base_url || ''}
						oninput={(e) =>
							onSettingChange('prompt_embedding_ollama_base_url', e.currentTarget.value)}
					/>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-ollama-model" class="block text-sm font-medium text-fg mb-1">
							Ollama model
						</label>
						<p class="text-sm text-fg-muted">Model served by the Ollama instance above</p>
					</div>
					<input
						id="prompt-embedding-ollama-model"
						type="text"
						class="input w-64 flex-shrink-0 font-mono text-sm"
						value={settings.prompt_embedding_ollama_model || ''}
						oninput={(e) => onSettingChange('prompt_embedding_ollama_model', e.currentTarget.value)}
					/>
				</div>
			{:else}
				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-device" class="block text-sm font-medium text-fg mb-1">
							Device
						</label>
						<p class="text-sm text-fg-muted">Device the local embedder runs on</p>
					</div>
					<select
						id="prompt-embedding-device"
						class="input w-48 flex-shrink-0"
						value={settings.prompt_embedding_device || 'cpu'}
						onchange={(e) => onSettingChange('prompt_embedding_device', e.currentTarget.value)}
					>
						<option value="cpu">CPU</option>
						<option value="cuda">CUDA</option>
					</select>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<label for="prompt-embedding-auto-download" class="block text-sm font-medium text-fg mb-1">
							Auto-download
						</label>
						<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
					</div>
					<input
						type="checkbox"
						id="prompt-embedding-auto-download"
						class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
						checked={settings.prompt_embedding_auto_download ?? false}
						onchange={(e) =>
							onSettingChange('prompt_embedding_auto_download', e.currentTarget.checked)}
					/>
				</div>

				<div class="flex items-start justify-between gap-6">
					<div>
						<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
						<p class="text-xs font-mono text-fg-muted truncate max-w-md">
							{fetch.state.prompt_embedding.path ?? '—'}
						</p>
					</div>
					<ModelFetchRow
						state={fetch.state.prompt_embedding}
						onFetch={() => fetch.fetchModel('prompt_embedding')}
					/>
				</div>
			{/if}
		</div>
	</div>
</div>
