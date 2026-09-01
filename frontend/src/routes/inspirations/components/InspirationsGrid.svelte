<script lang="ts">
	import { inspirationsStore, inspirationsTotalPages } from '$lib/stores/inspirations';
	import { hasActiveInspirationsFilters } from '$lib/inspirations/inspirationsQuery';
	import { Button, EmptyState, Pagination } from '$lib/components/ui';
	import type { InspirationDto } from '$lib/services/api/inspirations';
	import InspirationCard from './InspirationCard.svelte';
	import { layoutJustifiedRows, type JustifiedRow } from '$lib/utils/justifiedLayout';
	import { inspirationPrimaryMedia, inspirationAspect } from '$lib/inspirations/inspirationCardMeta';

	export let onOpen: (item: InspirationDto) => void;

	$: state = $inspirationsStore;
	$: items = state.items;
	$: pages = $inspirationsTotalPages;
	$: filtered = hasActiveInspirationsFilters(state.filters);

	// Same justified-rows treatment as the history grid: native aspect ratios,
	// uniform row heights, no center-crop squares (see HistoryGrid.svelte).
	const GAP = 12;
	let gridWidth = 0;

	$: targetRowHeight =
		gridWidth > 0 ? Math.round(Math.max(160, Math.min(280, gridWidth / 5.6))) : 220;

	$: rows = layoutJustifiedRows(
		items.map((item) => ({ item, aspect: inspirationAspect(inspirationPrimaryMedia(item)) })),
		gridWidth,
		targetRowHeight,
		GAP
	) as JustifiedRow<InspirationDto>[];

	// Skeleton layout reuses the real packer with a fixed aspect pattern so the
	// loading state already looks like the eventual light table.
	const SKELETON_ASPECTS = [1.5, 0.75, 1, 1.78, 1, 0.7, 1.33, 1, 0.75, 1.78, 1, 1.5];
	$: skeletonRows = layoutJustifiedRows(
		Array.from({ length: 10 }, (_, i) => ({
			item: i,
			aspect: SKELETON_ASPECTS[i % SKELETON_ASPECTS.length]
		})),
		gridWidth,
		targetRowHeight,
		GAP
	);

	async function handlePageChange(page: number) {
		inspirationsStore.setPage(page);
		await inspirationsStore.load();
	}

	function handleClearFilters() {
		inspirationsStore.clearFilters();
		inspirationsStore.load();
	}
</script>

<div class="px-3 py-3 md:px-6 md:py-6">
	<div bind:clientWidth={gridWidth}>
		{#if state.loading}
			{#if gridWidth > 0}
				<div class="space-y-3">
					{#each skeletonRows as row}
						<div class="flex" style="gap: {GAP}px">
							{#each row as box}
								<div
									class="rounded-lg bg-surface-2 animate-pulse border border-line"
									style="width: {box.width}px; height: {box.height}px"
								></div>
							{/each}
						</div>
					{/each}
				</div>
			{/if}
		{:else if items.length > 0 && gridWidth > 0}
			<div class="space-y-3">
				{#each rows as row}
					<div class="flex" style="gap: {GAP}px">
						{#each row as box (box.item.id)}
							<InspirationCard
								item={box.item}
								tile={{ width: box.width, height: box.height }}
								{onOpen}
							/>
						{/each}
					</div>
				{/each}
			</div>

			{#if pages > 1}
				<div class="mt-10 flex items-center justify-center">
					<Pagination
						currentPage={state.currentPage}
						totalPages={pages}
						onPageChange={handlePageChange}
					/>
				</div>
			{/if}
		{:else if items.length === 0}
			<EmptyState
				icon={filtered ? 'search' : 'lightbulb'}
				title={filtered ? 'No results found' : 'No inspirations yet'}
				description={filtered
					? 'Try a different search or folder to find what you are looking for.'
					: 'Publish a generation from its details to share it here, or browse what others have published.'}
			>
				{#snippet actions()}
					{#if filtered}
						<Button variant="secondary" size="sm" icon="close" onclick={handleClearFilters}>
							Clear filters
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}
	</div>
</div>
