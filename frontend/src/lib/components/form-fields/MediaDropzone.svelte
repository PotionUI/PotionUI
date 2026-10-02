<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import { describeDropTarget, describeFormats, type MediaKind } from './mediaLoaderConfig';
	import type { MediaSource } from './mediaFieldSources';

	let {
		kinds,
		dragging,
		pasteArmed,
		offersDraw,
		sizeHint,
		onSource
	}: {
		kinds: MediaKind[];
		dragging: boolean;
		pasteArmed: boolean;
		offersDraw: boolean;
		sizeHint: string | null;
		onSource: (source: MediaSource) => void;
	} = $props();

	let heading = $derived(kinds.length > 1 ? 'Drop media' : `Drop ${describeDropTarget(kinds)}`);
	let formats = $derived([describeFormats(kinds), sizeHint].filter(Boolean).join(' · '));
	let showPaste = $derived(kinds.includes('image'));
</script>

<div
	class="rounded-lg border border-dashed px-3 py-4 flex flex-col items-center gap-2.5 text-center transition-colors {dragging ||
	pasteArmed
		? 'border-signal bg-signal/10'
		: 'border-line-strong bg-surface-1'}"
	data-media-dropzone
>
	{#if dragging}
		<p class="text-sm text-signal py-3">Release to add</p>
	{:else}
		<div class="flex flex-wrap items-center justify-center gap-2 text-sm text-fg">
			<Icon name="upload" className="w-4 h-4 text-fg-subtle" strokeWidth={1.6} />
			<span>{heading} or</span>
			<Button variant="secondary" size="xs" onclick={() => onSource('browse')}>Browse</Button>
		</div>
		<p class="font-mono text-xs tabular-nums {pasteArmed ? 'text-signal' : 'text-fg-subtle'}">
			{pasteArmed ? 'Paste armed · press Ctrl V' : formats}
		</p>
		<div class="flex flex-wrap items-center justify-center gap-1">
			{#if showPaste}
				<Button variant="ghost" size="xs" icon="clipboard-list" onclick={() => onSource('paste')}>Paste</Button>
			{/if}
			<Button variant="ghost" size="xs" icon="clock" onclick={() => onSource('history')}>History</Button>
			<Button variant="ghost" size="xs" icon="grid" onclick={() => onSource('library')}>Library</Button>
			{#if offersDraw}
				<Button variant="ghost" size="xs" icon="brush" onclick={() => onSource('draw')}>Draw</Button>
			{/if}
		</div>
	{/if}
</div>
