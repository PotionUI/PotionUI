<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { OutputEntry } from '$lib/elixirs/types';
	import { stillSource, tileKind, tileName, videoSource } from '$lib/elixirs/tiles';

	interface Props {
		asset: Partial<OutputEntry>;
	}

	let { asset }: Props = $props();

	let failed = $state(false);
	let name = $derived(tileName(asset));
	let kind = $derived(tileKind(asset));
	let still = $derived(stillSource(asset));
	let clip = $derived(kind === 'video' ? videoSource(asset) : null);
</script>

<figure class="flex min-w-0 flex-col gap-1">
	<div class="relative aspect-square w-full overflow-hidden rounded border border-line bg-surface-2">
		{#if clip && !failed}
			<video src={clip} class="h-full w-full object-cover" muted playsinline preload="metadata" aria-label={name} onerror={() => (failed = true)}></video>
		{:else if still && !failed}
			<img src={still} alt={name} class="h-full w-full object-cover" loading="lazy" onerror={() => (failed = true)} />
		{:else}
			<div class="flex h-full w-full items-center justify-center text-fg-subtle">
				<Icon name={kind === 'video' ? 'film' : 'image'} className="h-5 w-5" />
			</div>
		{/if}
	</div>
	<figcaption class="truncate text-sm text-fg-muted">{name}</figcaption>
</figure>
