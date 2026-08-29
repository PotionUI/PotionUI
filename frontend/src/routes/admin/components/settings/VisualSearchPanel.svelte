<script lang="ts">
	import { createModelFetchController, modelNameLookup } from './modelFetch.svelte';
	import ModelFetchRow from './ModelFetchRow.svelte';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	const fetch = createModelFetchController(['media_vision'], modelNameLookup(settings));
</script>

<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
	<div class="px-6 py-3 border-b border-line">
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Visual Search</h3>
	</div>

	<div class="px-6 divide-y divide-line">
		<div class="py-4 space-y-4">
			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-model" class="block text-sm font-medium text-fg mb-1">Model</label>
					<p class="text-sm text-fg-muted">Hugging Face id of the SigLIP checkpoint</p>
				</div>
				<input
					id="media-vision-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.media_vision_model || ''}
					oninput={(e) => onSettingChange('media_vision_model', e.currentTarget.value)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-device" class="block text-sm font-medium text-fg mb-1">Device</label>
					<p class="text-sm text-fg-muted">Device the vision embedder runs on</p>
				</div>
				<select
					id="media-vision-device"
					class="input w-48 flex-shrink-0"
					value={settings.media_vision_device || 'cpu'}
					onchange={(e) => onSettingChange('media_vision_device', e.currentTarget.value)}
				>
					<option value="cpu">CPU</option>
					<option value="cuda">CUDA</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-vision-auto-download" class="block text-sm font-medium text-fg mb-1">
						Auto-download
					</label>
					<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
				</div>
				<input
					type="checkbox"
					id="media-vision-auto-download"
					class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
					checked={settings.media_vision_auto_download ?? false}
					onchange={(e) => onSettingChange('media_vision_auto_download', e.currentTarget.checked)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
					<p class="text-xs font-mono text-fg-muted truncate max-w-md">
						{fetch.state.media_vision.path ?? '—'}
					</p>
				</div>
				<ModelFetchRow state={fetch.state.media_vision} onFetch={() => fetch.fetchModel('media_vision')} />
			</div>
		</div>
	</div>
</div>
