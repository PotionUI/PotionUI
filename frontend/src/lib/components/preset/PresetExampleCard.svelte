<script lang="ts">
	import { Card, Badge, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import type { PresetGalleryItem } from '$lib/types/api';
	import { exampleAltText, isVideoExample } from '$lib/utils/presetMedia';
	import { copyText } from '$lib/utils/clipboard';

	export let presetId: string;
	export let presetName: string;
	export let item: PresetGalleryItem;
	export let onSelect: (() => void) | undefined = undefined;

	let copied = false;

	async function copyPrompt(e: MouseEvent) {
		e.stopPropagation();
		if (!item.prompt) return;
		const ok = await copyText(item.prompt);
		if (ok) {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		}
	}

	// A grid tile is a thumbnail, never a full render - 'small' is plenty for
	// the fixed aspect-square box below. A video item gets its poster frame
	// (same size, rendered server-side) rather than the video itself; there is
	// no in-grid playback, so the real file is never fetched here.
	$: mediaUrl = api.getPresetAssetURL(presetId, item.src, 'small');
	$: isVideo = isVideoExample(item);
</script>

<Card padding="none" interactive={!!onSelect} onclick={onSelect} class="overflow-hidden flex flex-col">
	<div class="relative w-full aspect-square bg-surface-2">
		<img
			src={mediaUrl}
			alt={exampleAltText(presetName, item.caption)}
			class="w-full h-full object-cover"
			loading="lazy"
		/>
		{#if isVideo}
			<div class="absolute inset-0 flex items-center justify-center pointer-events-none">
				<div class="w-9 h-9 rounded-full bg-surface-1/80 flex items-center justify-center text-fg shadow-raised">
					<Icon name="play" className="w-4 h-4" strokeWidth={2} />
				</div>
			</div>
		{/if}
	</div>

	<div class="p-3 flex flex-col gap-1.5">
		{#if item.caption}
			<p class="text-sm text-fg font-medium truncate">{item.caption}</p>
		{/if}

		{#if item.prompt}
			<div class="flex items-start gap-1.5">
				<p class="text-xs text-fg-muted line-clamp-2 flex-1">{item.prompt}</p>
				<IconButton
					icon={copied ? 'check' : 'copy'}
					label="Copy prompt"
					size="sm"
					onclick={copyPrompt}
				/>
			</div>
		{/if}

		{#if item.seed != null || item.mode}
			<div class="flex items-center gap-2 mt-0.5">
				{#if item.seed != null}
					<span class="text-2xs font-mono tabular-nums text-fg-subtle">seed {item.seed}</span>
				{/if}
				{#if item.mode}
					<Badge variant="neutral" size="sm">{item.mode}</Badge>
				{/if}
			</div>
		{/if}
	</div>
</Card>
