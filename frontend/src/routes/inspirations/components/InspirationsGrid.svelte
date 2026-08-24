<script lang="ts">
	import { inspirationsStore, inspirationsTotalPages } from '$lib/stores/inspirations';
	import { hasActiveInspirationsFilters } from '$lib/inspirations/inspirationsQuery';
	import { Button, EmptyState, Pagination } from '$lib/components/ui';
	import type { InspirationDto } from '$lib/services/api/inspirations';
	import InspirationCard from './InspirationCard.svelte';

	export let onOpen: (item: InspirationDto) => void;

	$: state = $inspirationsStore;
	$: items = state.items;
	$: pages = $inspirationsTotalPages;
	$: filtered = hasActiveInspirationsFilters(state.filters);

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
	{#if state.loading}
		<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
			{#each Array.from({ length: 10 }) as _}
				<div class="rounded-lg bg-surface-2 animate-pulse border border-line aspect-[4/3]"></div>
			{/each}
		</div>
	{:else if items.length > 0}
		<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
			{#each items as item (item.id)}
				<InspirationCard {item} {onOpen} />
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
	{:else}
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
