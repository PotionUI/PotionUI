<script module lang="ts">
	export type HeaderStage = 'full' | 'mid' | 'tight' | 'narrow';
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';

	let {
		stage = $bindable('full'),
		midBelow = 1100,
		tightBelow = 900,
		leading,
		title,
		search,
		facets,
		filters,
		sort,
		view,
		secondary,
		primary,
		more
	}: {
		stage?: HeaderStage;
		midBelow?: number;
		tightBelow?: number;
		leading?: Snippet;
		title: Snippet<[HeaderStage]>;
		search?: Snippet<[HeaderStage]>;
		facets?: Snippet<[HeaderStage]>;
		filters?: Snippet<[HeaderStage]>;
		sort?: Snippet<[HeaderStage]>;
		view?: Snippet<[HeaderStage]>;
		secondary?: Snippet<[HeaderStage]>;
		primary?: Snippet<[HeaderStage]>;
		more?: Snippet<[HeaderStage]>;
	} = $props();

	let width = $state(0);

	$effect(() => {
		if (width === 0) return;
		stage = width < 560 ? 'narrow' : width < tightBelow ? 'tight' : width < midBelow ? 'mid' : 'full';
	});

	const narrow = $derived(stage === 'narrow');
</script>

<div bind:clientWidth={width} class="flex-shrink-0">
	<PageHeader sticky={false}>
		{#if narrow}
			<div class="flex w-full flex-wrap items-center gap-x-2 gap-y-2">
				{@render leading?.()}
				{@render title(stage)}
				{@render primary?.(stage)}
				{@render more?.(stage)}
				<div class="flex w-full items-center gap-2">
					{@render search?.(stage)}
					{@render filters?.(stage)}
				</div>
			</div>
		{:else}
			<div class="flex w-full items-center gap-2">
				{@render leading?.()}
				{@render title(stage)}
				{@render search?.(stage)}
				{@render facets?.(stage)}
				{@render filters?.(stage)}
				<span class="min-w-2 flex-1"></span>
				{@render sort?.(stage)}
				{@render view?.(stage)}
				{@render secondary?.(stage)}
				{@render primary?.(stage)}
				{@render more?.(stage)}
			</div>
		{/if}
	</PageHeader>
</div>
