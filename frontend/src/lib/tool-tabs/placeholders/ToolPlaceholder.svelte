<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { ToolTabHost } from '../types';
	import { mediaFileStem } from '../sources';

	let {
		host,
		media,
		icon,
		emptyTitle,
		emptyText
	}: {
		host: ToolTabHost;
		media: 'image' | 'video';
		icon: string;
		emptyTitle: string;
		emptyText: string;
	} = $props();

	let url = $derived(host.source?.url ?? null);
	let stem = $derived(mediaFileStem(host.source));

	onMount(() => {
		host.setActions(null);
	});

	$effect(() => {
		if (stem) host.setTitle(stem);
		if (media === 'image') host.setThumbnail(url);
	});
</script>

<div class="tool-placeholder relative flex h-full w-full min-h-0 flex-col" data-testid="tool-tab-placeholder" data-tool={host.tool}>
	<div class="flex h-11 flex-shrink-0 items-center gap-2 border-b border-line px-4">
		<span class="truncate font-mono text-sm text-fg-muted">{stem ?? (media === 'image' ? 'No picture' : 'No clip')}</span>
	</div>
	<div class="dot-grid flex min-h-0 flex-1 items-center justify-center p-6">
		{#if url}
			{#if media === 'video'}
				<video src={url} class="max-h-full max-w-full rounded-lg shadow-raised" controls preload="metadata" data-testid="tool-tab-source"><track kind="captions" /></video>
			{:else}
				<img src={url} alt={stem ?? 'Source'} class="max-h-full max-w-full rounded-lg object-contain shadow-raised" data-testid="tool-tab-source" />
			{/if}
		{:else}
			<div class="flex w-full max-w-md flex-col items-center rounded-lg border border-dashed border-line-strong bg-surface-1 px-8 py-10 text-center" data-testid="tool-tab-empty">
				<span class="mb-4 flex h-11 w-11 items-center justify-center rounded border border-line bg-surface-2 text-fg-muted">
					<Icon name={icon} className="h-5 w-5" />
				</span>
				<h2 class="text-base font-semibold text-fg">{emptyTitle}</h2>
				<p class="mt-2 text-sm text-fg-muted">{emptyText}</p>
			</div>
		{/if}
	</div>
</div>
