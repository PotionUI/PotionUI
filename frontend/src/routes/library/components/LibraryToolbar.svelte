<script lang="ts">
	import { mediaGroupByDay } from '$lib/stores/mediaGroupByDay';
	import { onDestroy } from 'svelte';
	import { debounce } from '$lib/utils/time';
	import { libraryStore } from '$lib/stores/library';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { Menu } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderSegmented,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		HeaderAction,
		viewMenuItems,
		handleViewMenu,
		type HeaderStage,
		type FilterChip
	} from '$lib/components/page-header';
	import Icon from '$lib/components/Icon.svelte';
	import { hasConvertibleLibraryFilters, libraryFilterRule } from '$lib/organize/filterPrefill';
	import { startRuleFrom } from '$lib/organize/handoff';
	import CollectionSidebarSheet from '$lib/components/collections/CollectionSidebarSheet.svelte';
	import LibrarySidebar from './LibrarySidebar.svelte';
	import IncludeSubcollections from '$lib/components/collections/IncludeSubcollections.svelte';
	import { excludesSubcollections, hasSubcollections } from '$lib/components/collections/subcollections';
	import { libraryCollectionsStore } from '$lib/stores/collections';
	import type { LibrarySortDir, LibrarySortField } from '$lib/library/libraryQuery';

	export let onOpenAddTag: () => void;
	export let onPickFiles: () => void;
	export let onOpenDeleteByCriteria: () => void;
	export let sidebarOpen = true;
	export let onOpenSidebar: () => void = () => {};

	let stage: HeaderStage = 'full';
	let filtersOpen = false;

	$: state = $libraryStore;
	$: counts = state.mediaTypeCounts;
	$: collections = $libraryCollectionsStore.collections;
	$: subcollectionsShown = hasSubcollections(collections, state.filters.collectionId);
	$: excluding = excludesSubcollections(collections, state.filters.collectionId, !!state.filters.directOnly);

	function setDirectOnly(value: boolean) {
		libraryStore.setFilter('directOnly', value);
		libraryStore.load();
	}

	$: canMakeRule = hasConvertibleLibraryFilters(state.filters);

	function handleMakeRule() {
		const names = (ids: string[]) =>
			state.availableTags.filter((tag) => ids.includes(tag.id)).map((tag) => tag.name);
		return startRuleFrom(libraryFilterRule(state.filters, { tagNames: names }));
	}

	const sortFields: Array<{ value: string; label: string }> = [
		{ value: 'added', label: 'Added' },
		{ value: 'name', label: 'Name' },
		{ value: 'size', label: 'Size' }
	];
	const sortDefaultDir: Record<string, LibrarySortDir> = { added: 'desc', name: 'asc', size: 'desc' };

	$: sortField = state.filters.sortBy ?? 'added';
	$: sortDirection = state.filters.sortDir ?? sortDefaultDir[sortField];

	function handleSortFieldChange(value: string) {
		libraryStore.setFilter('sortBy', value as LibrarySortField);
		libraryStore.setFilter('sortDir', sortDefaultDir[value]);
		libraryStore.load();
	}

	function handleSortDirChange(dir: 'asc' | 'desc') {
		libraryStore.setFilter('sortBy', sortField as LibrarySortField);
		libraryStore.setFilter('sortDir', dir);
		libraryStore.load();
	}

	const mediaTypeLabels: Record<string, string> = { image: 'Images', video: 'Videos', audio: 'Audio' };

	$: mediaTypeItems = [
		{ value: 'all', label: 'All', tooltip: 'All media' },
		{ value: 'image', icon: 'image', tooltip: 'Images', count: counts.image || undefined },
		{ value: 'video', icon: 'video', tooltip: 'Videos', count: counts.video || undefined },
		{ value: 'audio', icon: 'audio', tooltip: 'Audio', count: counts.audio || undefined }
	];

	async function handleMediaTypeChange(value: string) {
		libraryStore.setFilter('mediaType', value as 'all' | 'image' | 'video' | 'audio');
		await libraryStore.load();
	}

	function buildChips(mediaType: string, excludingSub: boolean): FilterChip[] {
		const list: FilterChip[] = [];
		if (mediaType !== 'all') {
			list.push({ id: 'media', label: mediaTypeLabels[mediaType] ?? mediaType, onremove: () => handleMediaTypeChange('all') });
		}
		if (excludingSub) {
			list.push({ id: 'subcollections', label: 'Without subcollections', onremove: () => setDirectOnly(false) });
		}
		return list;
	}

	$: chips = buildChips(state.filters.mediaType, excluding);

	function clearAll() {
		libraryStore.setFilter('mediaType', 'all');
		libraryStore.setFilter('directOnly', false);
		libraryStore.load();
	}

	const scheduleSearch = debounce(() => libraryStore.load(), 300);
	function handleSearchInput(value: string) {
		libraryStore.setFilter('search', value);
		scheduleSearch();
	}

	onDestroy(scheduleSearch.cancel);

	$: moreItems = [
		...(stage === 'narrow'
			? viewMenuItems({ size: $historyTileSize, grouped: $mediaGroupByDay, refresh: true })
			: []),
		{ id: 'tag', label: 'Add tag', icon: 'plus' },
		{ id: 'delete-separator', label: '', separator: true },
		{ id: 'delete', label: 'Delete by criteria', icon: 'trash', danger: true }
	];

	function handleMoreSelect(id: string) {
		const handled = handleViewMenu(id, {
			onsize: (size) => historyTileSize.set(size as HistoryTileSize),
			ongroup: () => mediaGroupByDay.update((value) => !value),
			onrefresh: () => libraryStore.load()
		});
		if (handled) return;
		if (id === 'tag') onOpenAddTag();
		else if (id === 'delete') onOpenDeleteByCriteria();
	}

	$: showFilters = subcollectionsShown || canMakeRule || stage === 'tight' || stage === 'narrow';
</script>

<ListPageHeader bind:stage>
	{#snippet leading()}
		<CollectionSidebarSheet label="Library" collapsed={!sidebarOpen} onExpand={onOpenSidebar}>
			<LibrarySidebar />
		</CollectionSidebarSheet>
	{/snippet}

	{#snippet title(s)}
		<HeaderTitle
			title="Library"
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
			placeholder="Search by file name..."
			oninput={handleSearchInput}
			narrow={s === 'narrow'}
		/>
	{/snippet}

	{#snippet facets(s)}
		{#if s !== 'tight' && s !== 'narrow'}
			<HeaderSegmented
				label="Media type"
				items={mediaTypeItems}
				value={state.filters.mediaType}
				onchange={handleMediaTypeChange}
			/>
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
							<span class="block text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1">Media type</span>
							<HeaderSegmented
								label="Media type"
								items={mediaTypeItems}
								value={state.filters.mediaType}
								onchange={handleMediaTypeChange}
							/>
						</div>
					{/if}
					<IncludeSubcollections
						{collections}
						activeId={state.filters.collectionId}
						directOnly={!!state.filters.directOnly}
						onDirectOnlyChange={setDirectOnly}
					/>
				{/snippet}
				{#snippet footerAction(close)}
					{#if canMakeRule}
						<button
							type="button"
							class="flex items-center gap-1 text-sm text-fg-muted hover:text-fg transition-colors"
							on:click={() => {
								close();
								handleMakeRule();
							}}
						>
							<Icon name="sparkles" className="w-3 h-3" />
							Make a rule from this filter
						</button>
					{/if}
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
		<LibrarySidebar part="actions" />
		<HeaderViewOptions onrefresh={() => libraryStore.load()} />
	{/snippet}

	{#snippet primary(s)}
		<HeaderAction
			primary
			icon="upload"
			label="Upload"
			compact={s === 'narrow'}
			loading={state.uploading}
			disabled={state.uploading}
			onclick={onPickFiles}
		/>
	{/snippet}

	{#snippet more()}
		<Menu label="More actions" tooltip="More actions" items={moreItems} onselect={handleMoreSelect} width={200} />
	{/snippet}
</ListPageHeader>
