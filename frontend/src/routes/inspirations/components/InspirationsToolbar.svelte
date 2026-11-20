<script lang="ts">
	import { onDestroy } from 'svelte';
	import { debounce } from '$lib/utils/time';
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { mediaGroupByDay } from '$lib/stores/mediaGroupByDay';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { Menu } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		viewMenuItems,
		handleViewMenu,
		type HeaderStage,
		type FilterChip
	} from '$lib/components/page-header';
	import Icon from '$lib/components/Icon.svelte';
	import CollectionSidebarSheet from '$lib/components/collections/CollectionSidebarSheet.svelte';
	import InspirationsSidebar from './InspirationsSidebar.svelte';
	import IncludeSubcollections from '$lib/components/collections/IncludeSubcollections.svelte';
	import { excludesSubcollections, hasSubcollections } from '$lib/components/collections/subcollections';
	import { inspirationsCollectionsStore } from '$lib/stores/inspirationsCollections';
	import type { InspirationsSortDir, InspirationsSortField } from '$lib/inspirations/inspirationsQuery';

	export let sidebarOpen = true;
	export let onOpenSidebar: () => void = () => {};

	let stage: HeaderStage = 'full';
	let filtersOpen = false;

	$: state = $inspirationsStore;
	$: collections = $inspirationsCollectionsStore.collections;
	$: subcollectionsShown = hasSubcollections(collections, state.filters.collectionId);
	$: excluding = excludesSubcollections(collections, state.filters.collectionId, !!state.filters.directOnly);

	function setDirectOnly(value: boolean) {
		inspirationsStore.setFilter('directOnly', value);
		inspirationsStore.load();
	}

	const scheduleSearch = debounce(() => inspirationsStore.load(), 300);
	function handleSearchInput(value: string) {
		inspirationsStore.setFilter('search', value);
		scheduleSearch();
	}

	onDestroy(scheduleSearch.cancel);

	async function toggleSaved() {
		inspirationsStore.setFilter('saved', !state.filters.saved);
		await inspirationsStore.load();
	}

	const sortFields: Array<{ value: string; label: string }> = [
		{ value: 'created', label: 'Created' },
		{ value: 'title', label: 'Title' },
		{ value: 'saved', label: 'Most saved' },
		{ value: 'comments', label: 'Most commented' }
	];
	const sortDefaultDir: Record<string, InspirationsSortDir> = {
		created: 'desc',
		title: 'asc',
		saved: 'desc',
		comments: 'desc'
	};

	$: sortField = state.filters.sortBy ?? 'created';
	$: sortDirection = state.filters.sortDir ?? sortDefaultDir[sortField];

	function handleSortFieldChange(value: string) {
		inspirationsStore.setFilter('sortBy', value as InspirationsSortField);
		inspirationsStore.setFilter('sortDir', sortDefaultDir[value]);
		inspirationsStore.load();
	}

	function handleSortDirChange(dir: 'asc' | 'desc') {
		inspirationsStore.setFilter('sortBy', sortField as InspirationsSortField);
		inspirationsStore.setFilter('sortDir', dir);
		inspirationsStore.load();
	}

	function buildChips(saved: boolean, excludingSub: boolean): FilterChip[] {
		const list: FilterChip[] = [];
		if (saved) list.push({ id: 'saved', label: 'Saved', onremove: toggleSaved });
		if (excludingSub) list.push({ id: 'subcollections', label: 'Without subcollections', onremove: () => setDirectOnly(false) });
		return list;
	}

	$: chips = buildChips(state.filters.saved, excluding);

	function clearAll() {
		inspirationsStore.setFilter('saved', false);
		inspirationsStore.setFilter('directOnly', false);
		inspirationsStore.load();
	}

	$: showFilters = subcollectionsShown || stage === 'tight' || stage === 'narrow';

	$: moreItems =
		stage === 'narrow'
			? viewMenuItems({ size: $historyTileSize, grouped: $mediaGroupByDay, refresh: true })
			: [];

	function handleMoreSelect(id: string) {
		handleViewMenu(id, {
			onsize: (size) => historyTileSize.set(size as HistoryTileSize),
			ongroup: () => mediaGroupByDay.update((value) => !value),
			onrefresh: () => inspirationsStore.load()
		});
	}
</script>

<ListPageHeader bind:stage>
	{#snippet leading()}
		<CollectionSidebarSheet label="Inspirations" collapsed={!sidebarOpen} onExpand={onOpenSidebar}>
			<InspirationsSidebar />
		</CollectionSidebarSheet>
	{/snippet}

	{#snippet title(s)}
		<HeaderTitle
			title="Inspirations"
			count={state.totalCount}
			unit="items"
			unitOne="item"
			filterCount={showFilters ? chips.length : 0}
			onOpenFilters={() => (filtersOpen = true)}
			showUnit={s === 'full'}
			narrow={s === 'narrow'}
		/>
	{/snippet}

	{#snippet search(s)}
		<HeaderSearch
			value={state.filters.search}
			placeholder="Search title or description..."
			oninput={handleSearchInput}
			narrow={s === 'narrow'}
		/>
	{/snippet}

	{#snippet facets(s)}
		{#if s !== 'tight' && s !== 'narrow'}
			<button
				type="button"
				class="inline-flex h-8 flex-none items-center gap-1.5 rounded border px-2.5 text-sm transition-colors duration-100 {state
					.filters.saved
					? 'border-signal/25 bg-signal/10 text-signal'
					: 'border-line-strong bg-surface-2/50 text-fg hover:bg-surface-3/60'}"
				aria-pressed={state.filters.saved}
				on:click={toggleSaved}
			>
				<Icon name="save" className="w-3.5 h-3.5" />
				Saved
			</button>
		{/if}
	{/snippet}

	{#snippet filters(s)}
		{#if showFilters}
			<HeaderFilters
				bind:open={filtersOpen}
				activeCount={chips.length}
				{chips}
				onClear={clearAll}
				iconOnly={s === 'narrow'}
			>
				{#snippet children()}
					{#if s === 'narrow'}
						<div class="col-span-2">
							<span class="block text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1">Sort</span>
							<HeaderSort
								options={sortFields}
								value={sortField}
								dir={sortDirection}
								onchange={handleSortFieldChange}
								ondir={handleSortDirChange}
							/>
						</div>
					{/if}
					{#if s === 'tight' || s === 'narrow'}
						<div class="col-span-2">
							<button
								type="button"
								class="input flex items-center gap-1.5 text-left text-sm py-1.5 px-2 bg-surface-3/50 {state.filters.saved
									? 'text-signal'
									: 'text-fg-muted'}"
								aria-pressed={state.filters.saved}
								on:click={toggleSaved}
							>
								<Icon name="save" className="w-3.5 h-3.5" />
								Saved only
							</button>
						</div>
					{/if}
					<IncludeSubcollections
						{collections}
						activeId={state.filters.collectionId}
						directOnly={!!state.filters.directOnly}
						onDirectOnlyChange={setDirectOnly}
					/>
				{/snippet}
			</HeaderFilters>
		{/if}
	{/snippet}

	{#snippet sort()}
		<HeaderSort
			options={sortFields}
			value={sortField}
			dir={sortDirection}
			onchange={handleSortFieldChange}
			ondir={handleSortDirChange}
		/>
	{/snippet}

	{#snippet view()}
		<HeaderViewOptions
			size={$historyTileSize}
			onsize={(size) => historyTileSize.set(size as HistoryTileSize)}
			grouped={$mediaGroupByDay}
			ongroup={() => mediaGroupByDay.update((value) => !value)}
		/>
		<InspirationsSidebar part="actions" />
		<HeaderViewOptions onrefresh={() => inspirationsStore.load()} />
	{/snippet}

	{#snippet more()}
		{#if moreItems.length > 0}
			<Menu label="More actions" tooltip="More actions" items={moreItems} onselect={handleMoreSelect} width={200} />
		{/if}
	{/snippet}
</ListPageHeader>
