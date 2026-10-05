<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import MediaPreview from '$lib/components/MediaPreview.svelte';
	import type { GenerationFile } from '$lib/types/history';

	export let file: GenerationFile;
	export let generationId: string;
	export let label: string;
	export let sublabel: string = '';
	export let mediaType: string | undefined = undefined;
	export let onPick: () => void;
</script>

<div class="flex flex-col" data-testid="history-file-tile" data-file-id={file.id}>
	<button
		type="button"
		class="relative aspect-video w-full overflow-hidden rounded-lg border-2 border-line-strong bg-surface-2 transition-colors hover:border-line-hover focus-visible:border-signal"
		aria-label={`Select ${label}`}
		on:click={onPick}
	>
		<span class="flex h-full w-full items-center justify-center">
			{#if mediaType === 'image' || mediaType === 'video'}
				<MediaPreview {file} {generationId} className="w-full h-full" loadFullOnClick={false} />
			{:else}
				<Icon name={mediaType === 'audio' ? 'audio' : 'cube'} className="w-8 h-8 text-fg-muted" />
			{/if}
		</span>
		<span class="pointer-events-none absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-2 pt-4 text-left">
			<span class="block truncate text-sm font-medium text-white">{label}</span>
		</span>
	</button>
	{#if sublabel}
		<p class="mt-1 truncate font-mono text-xs tabular-nums text-fg-subtle">{sublabel}</p>
	{/if}
</div>

<style>
	button:focus-visible {
		outline: none;
		box-shadow: 0 0 0 2px rgb(var(--signal));
	}
</style>
