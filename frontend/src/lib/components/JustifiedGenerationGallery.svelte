<script lang="ts">
	import GenerationCard from '$lib/components/GenerationCard.svelte';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { layoutJustifiedRows, clampAspect, type JustifiedRow } from '$lib/utils/justifiedLayout';
	import { leadIndex } from '$lib/generation/leadFile';

	// Justified light-table of generations (native aspect ratios, uniform row
	// heights) — the same treatment as the history page, reusable on any page
	// that lists generations (e.g. model detail).
	export let generations: GenerationHistoryItem[] = [];
	export let onOpen: (generation: GenerationHistoryItem, fileIndex: number) => void;
	export let showActions: boolean = false;

	const GAP = 12;
	let width = 0;

	$: targetRowHeight = width > 0 ? Math.max(180, Math.min(320, Math.round(width / 4.6))) : 260;

	function aspectOf(generation: GenerationHistoryItem): number {
		// Same lead-file rule as GenerationCard (newest derived file leads).
		const media = generation.files.filter(
			(f) => f.is_final !== false && ['image', 'video'].includes(f.file_type.toLowerCase())
		);
		const file = media[leadIndex(media)];
		if (file?.width && file?.height) return clampAspect(file.width / file.height);
		return 1;
	}

	$: rows = layoutJustifiedRows(
		generations.map((item) => ({ item, aspect: aspectOf(item) })),
		width,
		targetRowHeight,
		GAP
	) as JustifiedRow<GenerationHistoryItem>[];

	function handleImageClick(event: CustomEvent) {
		const generation = generations.find((g) => g.id === event.detail.generationId);
		if (generation) onOpen(generation, 0);
	}

	function handleViewClick(event: CustomEvent<GenerationHistoryItem>) {
		onOpen(event.detail, 0);
	}
</script>

<div bind:clientWidth={width}>
	{#if width > 0}
		<div class="space-y-3">
			{#each rows as row}
				<div class="flex" style="gap: {GAP}px">
					{#each row as box (box.item.id)}
						<GenerationCard
							generation={box.item}
							tile={{ width: box.width, height: box.height }}
							{showActions}
							on:imageClick={handleImageClick}
							on:viewClick={handleViewClick}
						/>
					{/each}
				</div>
			{/each}
		</div>
	{/if}
</div>
