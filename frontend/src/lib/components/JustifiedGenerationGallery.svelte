<script lang="ts">
	import GenerationCard from '$lib/components/GenerationCard.svelte';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import {
		layoutJustifiedRows,
		flattenJustifiedRows,
		clampAspect,
		type FlatJustifiedBox
	} from '$lib/utils/justifiedLayout';
	import { leadIndex } from '$lib/generation/leadFile';
	import { createHistoryEntryMenu } from '$lib/tools/entryMenu';
	import { registerEntryTools } from '$lib/tools/entryTools';
	import type { ToolRunHost } from '$lib/tools/tools';

	registerEntryTools();

	// Justified light-table of generations (native aspect ratios, uniform row
	// heights) — the same treatment as the history page, reusable on any page
	// that lists generations (e.g. model detail).
	export let generations: GenerationHistoryItem[] = [];
	export let onOpen: (generation: GenerationHistoryItem, fileIndex: number) => void = () => {};
	export let itemKey: (generation: GenerationHistoryItem) => string = (generation) => generation.id;
	export let showActions: boolean = false;
	export let onReuse: ((generation: GenerationHistoryItem) => void) | null = null;

	const GAP = 12;
	let width = 0;

	$: targetRowHeight = width > 0 ? Math.max(180, Math.min(320, Math.round(width / 4.6))) : 260;

	function aspectOf(generation: GenerationHistoryItem): number {
		// Same lead-file rule as GenerationCard (newest derived file leads).
		const media = generation.files.filter(
			(f) => f.is_final !== false && ['image', 'video'].includes((f.file_type ?? '').toLowerCase())
		);
		const file = media[leadIndex(media)];
		if (file?.width && file?.height) return clampAspect(file.width / file.height);
		return 1;
	}

	$: boxes = (
		flattenJustifiedRows(
			layoutJustifiedRows(
				generations.map((item) => ({ item, aspect: aspectOf(item) })),
				width,
				targetRowHeight,
				GAP
			)
		) as FlatJustifiedBox<GenerationHistoryItem>[]
	).map((box) => ({ ...box, tile: { width: box.width, height: box.height } }));

	function handleImageClick(event: CustomEvent) {
		const generation = generations.find((g) => g.id === event.detail.generationId);
		if (generation) onOpen(generation, 0);
	}

	$: host = {
		open: (ctx) => ctx.generations[0] && onOpen(ctx.generations[0], 0),
		reuse: onReuse ? (ctx) => ctx.generations[0] && onReuse?.(ctx.generations[0]) : undefined
	} as ToolRunHost;

	$: entryMenu = createHistoryEntryMenu({
		generations: () => generations,
		selectedIds: () => [],
		host
	});

	function handleViewClick(event: CustomEvent<GenerationHistoryItem>) {
		onOpen(event.detail, 0);
	}
</script>

<div bind:clientWidth={width}>
	{#if width > 0}
		<div class="flex flex-wrap" style="gap: {GAP}px">
			{#each boxes as box (itemKey(box.item))}
				<div style="flex: {box.rowEnd ? '1 1' : '0 0'} {box.width}px">
					<slot name="card" generation={box.item} tile={box.tile}>
						<GenerationCard
							generation={box.item}
							tile={box.tile}
							{showActions}
							{entryMenu}
							on:imageClick={handleImageClick}
							on:viewClick={handleViewClick}
						/>
					</slot>
				</div>
			{/each}
		</div>
	{/if}
</div>
