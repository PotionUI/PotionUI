<script lang="ts">
	import { DetailSection } from '$lib/components/detail';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	function onNumberInput(key: string, e: Event) {
		const value = (e.currentTarget as HTMLInputElement).value;
		onSettingChange(key, value === '' ? null : Number(value));
	}
</script>

<DetailSection label="Content Safety" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="nsfw" class="block text-sm font-medium text-fg mb-1">NSFW Content</label>
				<p class="text-sm text-fg-muted">Allow generation of NSFW content</p>
			</div>
			<input
				type="checkbox"
				id="nsfw"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={settings.nsfw || false}
				onchange={(e) => onSettingChange('nsfw', e.currentTarget.checked)}
			/>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="media-nsfw-blur-threshold" class="block text-sm font-medium text-fg mb-1">
					NSFW blur threshold
				</label>
				<p class="text-sm text-fg-muted">
					Blur gallery media when questionable + explicit ratings reach this value
				</p>
			</div>
			<input
				id="media-nsfw-blur-threshold"
				type="number"
				min="0"
				max="1"
				step="0.05"
				class="input w-24 flex-shrink-0 font-mono tabular-nums text-sm"
				value={settings.media_nsfw_blur_threshold ?? 0.6}
				oninput={(e) => onNumberInput('media_nsfw_blur_threshold', e)}
			/>
		</div>
	</div>
</DetailSection>
