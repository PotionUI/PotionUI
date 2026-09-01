<script lang="ts">
	import { createModelFetchController, modelNameLookup } from './modelFetch.svelte';
	import ModelFetchRow from './ModelFetchRow.svelte';
	import { DetailSection } from '$lib/components/detail';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	const fetch = createModelFetchController(['media_tagger'], modelNameLookup(settings));

	function onNumberInput(key: string, e: Event) {
		const value = (e.currentTarget as HTMLInputElement).value;
		onSettingChange(key, value === '' ? null : Number(value));
	}
</script>

<DetailSection label="Media Tagging" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 space-y-4">
			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-model" class="block text-sm font-medium text-fg mb-1">Model</label>
					<p class="text-sm text-fg-muted">Hugging Face id of the local WD tagger checkpoint</p>
				</div>
				<input
					id="media-tagger-model"
					type="text"
					class="input w-64 flex-shrink-0 font-mono text-sm"
					value={settings.media_tagger_model || ''}
					oninput={(e) => onSettingChange('media_tagger_model', e.currentTarget.value)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-device" class="block text-sm font-medium text-fg mb-1">Device</label>
					<p class="text-sm text-fg-muted">Device the tagger model runs on</p>
				</div>
				<select
					id="media-tagger-device"
					class="input w-48 flex-shrink-0"
					value={settings.media_tagger_device || 'cpu'}
					onchange={(e) => onSettingChange('media_tagger_device', e.currentTarget.value)}
				>
					<option value="cpu">CPU</option>
					<option value="cuda">CUDA</option>
				</select>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-auto-download" class="block text-sm font-medium text-fg mb-1">
						Auto-download
					</label>
					<p class="text-sm text-fg-muted">Fetch weights automatically on first use</p>
				</div>
				<input
					type="checkbox"
					id="media-tagger-auto-download"
					class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
					checked={settings.media_tagger_auto_download ?? false}
					onchange={(e) => onSettingChange('media_tagger_auto_download', e.currentTarget.checked)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-tag-threshold" class="block text-sm font-medium text-fg mb-1">
						Tag threshold
					</label>
					<p class="text-sm text-fg-muted">Minimum confidence to store a general tag</p>
				</div>
				<input
					id="media-tagger-tag-threshold"
					type="number"
					min="0"
					max="1"
					step="0.05"
					class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
					value={settings.media_tagger_tag_threshold ?? 0.35}
					oninput={(e) => onNumberInput('media_tagger_tag_threshold', e)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="media-tagger-character-threshold" class="block text-sm font-medium text-fg mb-1">
						Character threshold
					</label>
					<p class="text-sm text-fg-muted">Minimum confidence to store a character tag</p>
				</div>
				<input
					id="media-tagger-character-threshold"
					type="number"
					min="0"
					max="1"
					step="0.05"
					class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
					value={settings.media_tagger_character_threshold ?? 0.75}
					oninput={(e) => onNumberInput('media_tagger_character_threshold', e)}
				/>
			</div>

			<div class="flex items-start justify-between gap-6">
				<div>
					<span class="block text-sm font-medium text-fg mb-1">Model weights</span>
					<p class="text-xs font-mono text-fg-muted truncate max-w-md">
						{fetch.state.media_tagger.path ?? '—'}
					</p>
				</div>
				<ModelFetchRow state={fetch.state.media_tagger} onFetch={() => fetch.fetchModel('media_tagger')} />
			</div>
		</div>
	</div>
</DetailSection>
