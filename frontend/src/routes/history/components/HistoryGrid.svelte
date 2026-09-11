<script lang="ts">
	import {
		historyStore,
		totalPages,
		filteredGenerations,
		HISTORY_ITEMS_PER_PAGE_OPTIONS
	} from '$lib/stores/history';
	import GenerationCard from '$lib/components/GenerationCard.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, Pagination } from '$lib/components/ui';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { layoutJustifiedRows, clampAspect, type JustifiedRow } from '$lib/utils/justifiedLayout';
	import { dayKey, dayLabel } from '$lib/utils/relativeTime';
	import { historyTileSize, TILE_SIZE_MULTIPLIER } from '$lib/stores/historyTileSize';
	import { nsfwFilterStore, selectableMediaFiles, isGenerationHiddenByNsfw } from '$lib/stores/nsfwFilter';
	import { leadIndex } from '$lib/generation/leadFile';
	import {
		classifyCardSelectEvent,
		marqueeSelection,
		resolveCardSelect,
		type MarqueeVisualState
	} from '$lib/selection/marquee';

	// Self-contained: reads/writes historyStore directly. Renders the page's
	// generations as a justified gallery (native aspect ratios, uniform row
	// heights) grouped by day. Delete confirmation modal lives on the page.
	export let onDeleteRequest: (generation: GenerationHistoryItem) => void;

	nsfwFilterStore.init();

	$: currentState = $historyStore;
	$: nsfwMode = $nsfwFilterStore.mode;
	$: generations = $filteredGenerations.filter(
		(generation) => !isGenerationHiddenByNsfw(selectableMediaFiles(generation.files ?? []), nsfwMode)
	);
	$: pages = $totalPages;

	const GAP = 12;
	let gridWidth = 0;

	// Preferred row height scales gently with viewport width; the user's
	// tile-size preference (S/M/L in the toolbar) multiplies it.
	$: targetRowHeight =
		Math.round(
			(gridWidth > 0 ? Math.max(180, Math.min(320, gridWidth / 4.6)) : 260) *
				TILE_SIZE_MULTIPLIER[$historyTileSize]
		);

	function aspectOf(generation: GenerationHistoryItem): number {
		// Same lead-file rule as GenerationCard, so the tile's aspect matches the
		// preview it actually shows (a derived enhance pass may differ in size).
		const media = generation.files.filter(
			(f) => f.is_final !== false && ['image', 'video', 'audio', 'mesh'].includes(f.file_type.toLowerCase())
		);
		const file = media[leadIndex(media)];
		if (file?.width && file?.height) return clampAspect(file.width / file.height);
		return 1;
	}

	interface DayGroup {
		key: string;
		label: string;
		items: GenerationHistoryItem[];
	}

	function buildGroups(items: GenerationHistoryItem[]): DayGroup[] {
		const groups: DayGroup[] = [];
		for (const generation of items) {
			const key = dayKey(generation.created_at);
			const last = groups[groups.length - 1];
			if (last && last.key === key) {
				last.items.push(generation);
			} else {
				groups.push({ key, label: dayLabel(generation.created_at), items: [generation] });
			}
		}
		return groups;
	}

	// When sorting by rating or file size the day grouping is meaningless, so we
	// render a single flat justified grid (no day headers).
	$: grouped =
		currentState.filters.sortBy !== 'rating' && currentState.filters.sortBy !== 'file_size';
	$: groups = grouped
		? buildGroups(generations)
		: generations.length > 0
			? [{ key: '__flat__', label: '', items: generations }]
			: [];
	$: groupLayouts = groups.map((group) => ({
		...group,
		rows: layoutJustifiedRows(
			group.items.map((item) => ({ item, aspect: aspectOf(item) })),
			gridWidth,
			targetRowHeight,
			GAP
		) as JustifiedRow<GenerationHistoryItem>[]
	}));

	// Row-major id order across every group, for shift-click range selection.
	$: orderedIds = groupLayouts.flatMap((group) =>
		group.rows.flatMap((row) => row.map((box) => box.item.id))
	);

	// Skeleton layout reuses the real packer with a fixed aspect pattern so the
	// loading state already looks like a light table.
	const SKELETON_ASPECTS = [1.5, 0.75, 1, 1.78, 1, 0.7, 1.33, 1, 0.75, 1.78, 1, 1.5];
	$: skeletonRows = layoutJustifiedRows(
		Array.from({ length: Math.min(currentState.itemsPerPage, 24) }, (_, i) => ({
			item: i,
			aspect: SKELETON_ASPECTS[i % SKELETON_ASPECTS.length]
		})),
		gridWidth,
		targetRowHeight,
		GAP
	);

	async function handlePageChange(page: number) {
		historyStore.setPage(page);
		await historyStore.loadGenerations();
	}

	async function handleItemsPerPageChange(itemsPerPage: number) {
		historyStore.setItemsPerPage(itemsPerPage);
		await historyStore.loadGenerations();
	}

	function handleViewGeneration(event: CustomEvent<GenerationHistoryItem>) {
		if (currentState.selectionMode) return;
		historyStore.setSelectedGeneration(event.detail, 0);
	}

	function handleImageClick(event: CustomEvent) {
		if (currentState.selectionMode) return;
		const generation = generations.find((g) => g.id === event.detail.generationId);
		if (generation) {
			historyStore.setSelectedGeneration(generation, 0);
		}
	}

	function handleDeleteClick(event: CustomEvent<GenerationHistoryItem>) {
		if (currentState.selectionMode) return;
		onDeleteRequest(event.detail);
	}

	function handleClearAllFilters() {
		historyStore.clearFilters();
		historyStore.loadGenerations();
	}

	$: hasActiveFilters =
		currentState.filters.status !== 'all' ||
		currentState.filters.datePreset !== 'all' ||
		currentState.filters.selectedTagIds.length > 0 ||
		currentState.filters.mediaType !== 'all' ||
		currentState.filters.search !== '';

	// --- Multi-select: click, shift-range, ctrl/cmd-toggle, and marquee drag ---
	// DOM wiring lives in `$lib/selection/marquee`; this just supplies the
	// row-major order and applies the selection it computes.

	let anchorId: string | null = null;
	let gridRootEl: HTMLDivElement;
	let marqueeState: MarqueeVisualState = { active: false, rect: null };

	function handleCardSelect(
		generation: GenerationHistoryItem,
		_file: unknown,
		event?: MouseEvent
	) {
		const { selection, nextAnchorId } = resolveCardSelect(
			classifyCardSelectEvent(event),
			orderedIds,
			anchorId,
			generation.id,
			currentState.selectedGenerationIds
		);
		anchorId = nextAnchorId;
		historyStore.setSelection(selection);
	}
</script>

<div class="px-3 py-3 md:px-6 md:py-6">
	<div
		bind:this={gridRootEl}
		bind:clientWidth={gridWidth}
		class="relative"
		class:select-none={marqueeState.active}
		role="listbox"
		aria-multiselectable={currentState.selectionMode}
		aria-label="Generation history"
		tabindex="-1"
		use:marqueeSelection={{
			orderedIds,
			selectedIds: currentState.selectedGenerationIds,
			cardSelector: '[data-history-card]',
			getCardId: (el) => el.dataset.historyCard,
			setSelection: (ids) => historyStore.setSelection(ids),
			onSelectAll: () => historyStore.selectAll(),
			onDragStart: () => (anchorId = null),
			onChange: (state) => (marqueeState = state)
		}}
	>
		{#if marqueeState.active && marqueeState.rect}
			<div
				class="fixed z-40 pointer-events-none rounded border border-signal bg-signal/10"
				style="left: {marqueeState.rect.left}px; top: {marqueeState.rect.top}px; width: {marqueeState
					.rect.right - marqueeState.rect.left}px; height: {marqueeState.rect.bottom -
					marqueeState.rect.top}px"
			></div>
		{/if}
		{#if currentState.loading}
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
		{:else if generations.length > 0 && gridWidth > 0}
			{#each groupLayouts as group (group.key)}
				<!-- Day header (hidden in flat/rating/size sort modes) -->
				{#if grouped}
					<div class="flex items-baseline gap-3 mb-3 mt-8 first:mt-0">
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted whitespace-nowrap">
							{group.label}
						</span>
						<span class="font-mono tabular-nums text-2xs text-fg-subtle whitespace-nowrap">
							{group.items.length}
						</span>
						<div class="flex-1 h-px bg-line self-center"></div>
					</div>
				{/if}

				<!-- Justified rows -->
				<div class="space-y-3">
					{#each group.rows as row}
						<div class="flex" style="gap: {GAP}px">
							{#each row as box (box.item.id)}
								<div
									data-history-card={box.item.id}
									style="width: {box.width}px"
									role="option"
									aria-selected={currentState.selectedGenerationIds.includes(box.item.id)}
								>
									<GenerationCard
										generation={box.item}
										tile={{ width: box.width, height: box.height }}
										on:imageClick={handleImageClick}
										on:viewClick={handleViewGeneration}
										on:deleteClick={handleDeleteClick}
										thumbnailSize="medium"
										showActions={!currentState.selectionMode}
										selectable={currentState.selectionMode}
										showCheckbox={true}
										selected={currentState.selectedGenerationIds.includes(box.item.id)}
										onSelect={handleCardSelect}
									/>
								</div>
							{/each}
						</div>
					{/each}
				</div>
			{/each}

			<!-- Pagination -->
			{#if pages > 1 || currentState.totalCount > currentState.itemsPerPage}
				<div class="mt-10 flex items-center justify-center gap-4 relative">
					<Pagination
						currentPage={currentState.currentPage}
						totalPages={pages}
						onPageChange={handlePageChange}
					/>

					<div class="hidden md:flex items-center gap-2 absolute right-0">
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Per page</span>
						<select
							class="input text-xs py-1 px-2 bg-surface-2/50 w-auto font-mono tabular-nums"
							value={currentState.itemsPerPage}
							on:change={(e) => handleItemsPerPageChange(parseInt(e.currentTarget.value))}
						>
							{#each HISTORY_ITEMS_PER_PAGE_OPTIONS as option}
								<option value={option}>{option}</option>
							{/each}
						</select>
					</div>
				</div>
			{/if}
		{:else if generations.length === 0}
			<EmptyState
				icon={hasActiveFilters ? 'search' : 'image'}
				title={hasActiveFilters ? 'No results found' : 'No generations yet'}
				description={hasActiveFilters
					? "Try adjusting your search criteria or filters to find what you're looking for."
					: 'Start creating AI-generated content to see your history here. Your creations will appear in this gallery.'}
			>
				{#snippet actions()}
					{#if hasActiveFilters}
						<Button variant="secondary" size="sm" icon="close" onclick={handleClearAllFilters}>
							Clear All Filters
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}
	</div>
</div>
