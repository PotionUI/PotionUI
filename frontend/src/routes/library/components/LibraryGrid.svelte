<script lang="ts">
	import { libraryStore, libraryTotalPages, LIBRARY_ITEMS_PER_PAGE_OPTIONS } from '$lib/stores/library';
	import { Button, EmptyState, Pagination } from '$lib/components/ui';
	import { layoutJustifiedRows, type JustifiedRow } from '$lib/utils/justifiedLayout';
	import { dayKey, dayLabel } from '$lib/utils/relativeTime';
	import { historyTileSize, TILE_SIZE_MULTIPLIER } from '$lib/stores/historyTileSize';
	import { hasActiveLibraryFilters } from '$lib/library/libraryQuery';
	import { libraryItemAspect } from '$lib/library/libraryItemMeta';
	import type { LibraryItem } from '$lib/services/api/library';
	import LibraryCard from './LibraryCard.svelte';

	// Self-contained: reads/writes libraryStore directly. Same justified layout
	// as the history gallery, grouped by the day the item entered the library.
	export let onDeleteRequest: (item: LibraryItem) => void;
	export let onUploadRequest: () => void;

	$: state = $libraryStore;
	$: items = state.items;
	$: pages = $libraryTotalPages;
	$: filtered = hasActiveLibraryFilters(state.filters);

	const GAP = 12;
	let gridWidth = 0;

	$: targetRowHeight =
		Math.round(
			(gridWidth > 0 ? Math.max(180, Math.min(320, gridWidth / 4.6)) : 260) *
				TILE_SIZE_MULTIPLIER[$historyTileSize]
		);

	interface DayGroup {
		key: string;
		label: string;
		items: LibraryItem[];
	}

	function buildGroups(list: LibraryItem[]): DayGroup[] {
		const groups: DayGroup[] = [];
		for (const item of list) {
			// An item with no created_at can't be dated; it joins the group above
			// it rather than opening a header nobody can read.
			const key = item.created_at ? dayKey(item.created_at) : '__undated__';
			const last = groups[groups.length - 1];
			if (last && last.key === key) {
				last.items.push(item);
			} else {
				groups.push({
					key,
					label: item.created_at ? dayLabel(item.created_at) : 'Undated',
					items: [item]
				});
			}
		}
		return groups;
	}

	$: groupLayouts = buildGroups(items).map((group) => ({
		...group,
		rows: layoutJustifiedRows(
			group.items.map((item) => ({ item, aspect: libraryItemAspect(item) })),
			gridWidth,
			targetRowHeight,
			GAP
		) as JustifiedRow<LibraryItem>[]
	}));

	const SKELETON_ASPECTS = [1.5, 0.75, 1, 1.78, 1, 0.7, 1.33, 1, 0.75, 1.78, 1, 1.5];
	$: skeletonRows = layoutJustifiedRows(
		Array.from({ length: Math.min(state.itemsPerPage, 24) }, (_, i) => ({
			item: i,
			aspect: SKELETON_ASPECTS[i % SKELETON_ASPECTS.length]
		})),
		gridWidth,
		targetRowHeight,
		GAP
	);

	async function handlePageChange(page: number) {
		libraryStore.setPage(page);
		await libraryStore.load();
	}

	async function handleItemsPerPageChange(itemsPerPage: number) {
		libraryStore.setItemsPerPage(itemsPerPage);
		await libraryStore.load();
	}

	function handleClearFilters() {
		libraryStore.clearFilters();
		libraryStore.load();
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
			{#each groupLayouts as group (group.key)}
				<div class="flex items-baseline gap-3 mb-3 mt-8 first:mt-0">
					<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
						{group.label}
					</span>
					<span class="font-mono tabular-nums text-2xs text-fg-subtle whitespace-nowrap">
						{group.items.length}
					</span>
					<div class="flex-1 h-px bg-line self-center"></div>
				</div>

				<div class="space-y-3">
					{#each group.rows as row}
						<div class="flex" style="gap: {GAP}px">
							{#each row as box (box.item.id)}
								<LibraryCard
									item={box.item}
									tile={{ width: box.width, height: box.height }}
									showActions={!state.selectionMode}
									selectable={state.selectionMode}
									selected={state.selectedIds.includes(box.item.id)}
									onSelect={(item) => libraryStore.toggleSelect(item.id)}
									on:open={(e) => libraryStore.setSelectedItem(e.detail)}
									on:delete={(e) => onDeleteRequest(e.detail)}
								/>
							{/each}
						</div>
					{/each}
				</div>
			{/each}

			{#if pages > 1 || state.totalCount > state.itemsPerPage}
				<div class="mt-10 flex items-center justify-center gap-4 relative">
					<Pagination
						currentPage={state.currentPage}
						totalPages={pages}
						onPageChange={handlePageChange}
					/>

					<div class="hidden md:flex items-center gap-2 absolute right-0">
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Per page</span>
						<select
							class="input text-xs py-1 px-2 bg-surface-2/50 w-auto font-mono tabular-nums"
							value={state.itemsPerPage}
							on:change={(e) => handleItemsPerPageChange(parseInt(e.currentTarget.value))}
						>
							{#each LIBRARY_ITEMS_PER_PAGE_OPTIONS as option}
								<option value={option}>{option}</option>
							{/each}
						</select>
					</div>
				</div>
			{/if}
		{:else if items.length === 0}
			<EmptyState
				icon={filtered ? 'search' : 'photo'}
				title={filtered ? 'No results found' : 'Your library is empty'}
				description={filtered
					? 'Try a different search, tag or folder to find what you are looking for.'
					: 'Upload media here, or copy something you generated across from your history. Anything in your library can be picked straight from a media field.'}
			>
				{#snippet actions()}
					{#if filtered}
						<Button variant="secondary" size="sm" icon="close" onclick={handleClearFilters}>
							Clear filters
						</Button>
					{:else}
						<Button variant="primary" size="sm" icon="upload" onclick={onUploadRequest}>
							Upload media
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}
	</div>
</div>
