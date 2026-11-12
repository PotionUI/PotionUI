<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, SegmentedControl } from '$lib/components/ui';
	import SearchableMultiSelectPopover from '$lib/components/selectors/SearchableMultiSelectPopover.svelte';
	import { buildTree, flattenTree } from '$lib/components/pane';
	import { filterBySearch, matchesSearch } from '$lib/components/selectors/searchFilter';
	import type { HistoryFacets, Tag } from '$lib/types/history';
	import type { PickerMediaType } from '../mediaPickerSelection';
	import PickerPopover from './PickerPopover.svelte';
	import PickerFiltersPanel from './PickerFiltersPanel.svelte';
	import TileSizeControl from './TileSizeControl.svelte';
	import {
		SORT_OPTIONS,
		hasFiltersButton,
		type HistorySort,
		type PickerFilters,
		type PickerSourceId,
		type PickerTier
	} from './pickerModel';
	import { kindFilterItems } from './pickerSelection';

	type CollectionNode = { id: string; name: string; parent_id?: string | null };

	const ALL = '__all__';
	const UNSORTED = '__unsorted__';

	let {
		source,
		filters,
		kinds,
		tier,
		tags,
		collections,
		collectionsLoading = false,
		facets,
		kitLabels,
		activeCount,
		popoverCount,
		onChange,
		onClear,
		onCollectionsOpen
	}: {
		source: PickerSourceId;
		filters: PickerFilters;
		kinds: readonly PickerMediaType[];
		tier: PickerTier;
		tags: Tag[];
		collections: CollectionNode[];
		collectionsLoading?: boolean;
		facets: HistoryFacets;
		kitLabels: string[];
		activeCount: number;
		popoverCount: number;
		onChange: (next: PickerFilters) => void;
		onClear: () => void;
		onCollectionsOpen?: () => void;
	} = $props();

	let kits = $derived(source === 'kits');
	let history = $derived(filters.history);
	let library = $derived(filters.library);
	let current = $derived(source === 'history' ? history : source === 'library' ? library : null);
	let search = $derived(source === 'history' ? history.search : source === 'library' ? library.search : filters.kits.search);
	let placeholder = $derived(kits ? 'Search kits' : source === 'history' ? 'Search prompt, preset, model' : 'Search file names');
	let showKind = $derived(!kits && kinds.length > 1 && tier !== 'sm');
	let showLabel = $derived(kits && tier !== 'sm' && kitLabels.length > 0);
	let inlineLists = $derived(!kits && tier === 'xl');
	let showFavorites = $derived(!kits && tier !== 'sm');
	let showFilters = $derived(hasFiltersButton(source, tier) && (!kits || kitLabels.length > 0));
	let showSort = $derived(source === 'history' && tier !== 'sm');

	let collectionOpen = $state(false);
	let collectionSearch = $state('');
	let tagsOpen = $state(false);
	let tagSearch = $state('');
	let filtersOpen = $state(false);

	let collectionId = $derived(source === 'library' ? (library.unsorted ? UNSORTED : library.collectionId) : history.collectionId);
	let nodes = $derived(
		filterBySearch(
			flattenTree(buildTree(collections as never[])) as Array<{ item: CollectionNode; depth: number }>,
			collectionSearch,
			(node) => node.item.name
		)
	);
	let collectionOptionIds = $derived([ALL, ...(source === 'library' ? [UNSORTED] : []), ...nodes.map((node) => node.item.id)]);
	let collectionName = $derived(
		collectionId === UNSORTED ? 'Unsorted' : collectionId ? (collections.find((entry) => entry.id === collectionId)?.name ?? '…') : null
	);
	let tagIds = $derived(current?.tagIds ?? []);
	let visibleTags = $derived(tags.filter((tag) => matchesSearch(tag.name, tagSearch)));
	let tagOptionIds = $derived(visibleTags.map((tag) => tag.id));

	function setSearch(value: string) {
		if (source === 'history') onChange({ ...filters, history: { ...history, search: value } });
		else if (source === 'library') onChange({ ...filters, library: { ...library, search: value } });
		else onChange({ ...filters, kits: { ...filters.kits, search: value } });
	}

	function setKind(id: string) {
		const kind = id as PickerFilters['history']['kind'];
		if (source === 'library') onChange({ ...filters, library: { ...library, kind } });
		else onChange({ ...filters, history: { ...history, kind } });
	}

	function setFavorites() {
		if (source === 'library') onChange({ ...filters, library: { ...library, favorites: !library.favorites } });
		else onChange({ ...filters, history: { ...history, favorites: !history.favorites } });
	}

	function pickCollection(id: string) {
		collectionOpen = false;
		if (source === 'library') {
			if (id === UNSORTED) onChange({ ...filters, library: { ...library, unsorted: true, collectionId: null } });
			else onChange({ ...filters, library: { ...library, unsorted: false, collectionId: id === ALL ? null : id } });
		} else {
			onChange({ ...filters, history: { ...history, collectionId: id === ALL ? null : id } });
		}
	}

	function toggleTag(id: string) {
		const next = tagIds.includes(id) ? tagIds.filter((tag) => tag !== id) : [...tagIds, id];
		if (source === 'library') onChange({ ...filters, library: { ...library, tagIds: next } });
		else onChange({ ...filters, history: { ...history, tagIds: next } });
	}

	function setSort(value: string) {
		onChange({ ...filters, history: { ...history, sort: value as HistorySort } });
	}

	const pill = (on: boolean) =>
		`inline-flex h-8 flex-shrink-0 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors ${on ? 'bg-signal/10 text-signal' : 'bg-surface-2 text-fg-muted hover:bg-surface-3'}`;
</script>

<div class="flex w-full items-center gap-2" data-picker-toolbar data-tier={tier}>
	<label class="input !flex !min-h-8 h-8 min-w-[9rem] flex-1 items-center gap-2 !px-2.5 !py-0 xl:max-w-[18rem]">
		<Icon name="search" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
		<input
			type="text"
			value={search}
			{placeholder}
			aria-label="Search"
			data-autofocus={tier === 'sm' ? undefined : ''}
			data-picker-search
			class="min-w-0 flex-1 bg-transparent text-sm text-fg placeholder-fg-subtle outline-none"
			oninput={(event) => setSearch(event.currentTarget.value)}
		/>
	</label>

	{#if showKind && current}
		<div class="flex-shrink-0" data-kind-filter>
			<SegmentedControl variant="toggle" ariaLabel="Kind" items={kindFilterItems(kinds)} selected={current.kind} onSelect={setKind} />
		</div>
	{/if}

	{#if showLabel}
		<div class="flex-shrink-0" data-label-filter>
			<SegmentedControl
				variant="toggle"
				ariaLabel="Label"
				items={[{ id: '', label: 'All' }, ...kitLabels.map((label) => ({ id: label, label }))]}
				selected={filters.kits.label ?? ''}
				onSelect={(id) => onChange({ ...filters, kits: { ...filters.kits, label: id || null } })}
			/>
		</div>
	{/if}

	{#if inlineLists}
		<SearchableMultiSelectPopover
			bind:open={collectionOpen}
			bind:searchValue={collectionSearch}
			panelClass="w-64"
			searchPlaceholder="Search collections"
			optionIds={collectionOptionIds}
			onOpen={onCollectionsOpen}
			onSelect={pickCollection}
		>
			{#snippet trigger({ open, toggle })}
				<button
					type="button"
					class="{pill(!!collectionName)} max-w-[12rem]"
					aria-haspopup="listbox"
					aria-expanded={open}
					data-collection-filter
					onclick={toggle}
				>
					<Icon name="folder" className="h-4 w-4 flex-shrink-0" />
					<span class="truncate">{collectionName ?? 'Collection'}</span>
					<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0" />
				</button>
			{/snippet}
			{#snippet panel({ activeId, optionId, listboxId })}
				<div id={listboxId} class="max-h-60 overflow-y-auto py-1" role="listbox" aria-label="Collections">
					{#snippet row(id: string, name: string, depth: number, icon: string)}
						<button
							type="button"
							id={optionId(id)}
							role="option"
							aria-selected={(collectionId ?? ALL) === id}
							class="flex w-full items-center gap-2 py-2 pr-3 text-left text-sm transition-colors {(collectionId ?? ALL) === id
								? 'bg-signal/10 text-signal'
								: 'text-fg hover:bg-surface-3/50'} {activeId === id ? 'ring-1 ring-inset ring-signal/50' : ''}"
							style="padding-left:{0.75 + depth * 0.9}rem"
							onclick={() => pickCollection(id)}
						>
							<Icon name={icon} className="h-4 w-4 flex-shrink-0" />
							<span class="min-w-0 flex-1 truncate">{name}</span>
							{#if (collectionId ?? ALL) === id}<Icon name="check" className="h-4 w-4" />{/if}
						</button>
					{/snippet}
					{@render row(ALL, source === 'library' ? 'All items' : 'All collections', 0, 'layers')}
					{#if source === 'library'}{@render row(UNSORTED, 'Unsorted', 0, 'folder-open')}{/if}
					{#if collectionsLoading && collections.length === 0}
						<p class="px-3 py-3 text-sm text-fg-subtle">Loading collections…</p>
					{:else if nodes.length === 0}
						<p class="px-3 py-3 text-sm text-fg-subtle">{collections.length === 0 ? 'No collections yet.' : 'No collections match.'}</p>
					{:else}
						{#each nodes as node (node.item.id)}
							{@render row(node.item.id, node.item.name, node.depth, 'folder')}
						{/each}
					{/if}
				</div>
			{/snippet}
		</SearchableMultiSelectPopover>

		<SearchableMultiSelectPopover
			bind:open={tagsOpen}
			bind:searchValue={tagSearch}
			panelClass="w-64"
			searchPlaceholder="Search tags"
			optionIds={tagOptionIds}
			onSelect={toggleTag}
		>
			{#snippet trigger({ open, toggle })}
				<button
					type="button"
					class={pill(tagIds.length > 0)}
					aria-haspopup="listbox"
					aria-expanded={open}
					data-filter-tags
					onclick={toggle}
				>
					<Icon name="tag" className="h-4 w-4" />
					<span>{tagIds.length === 0 ? 'Tags' : tagIds.length === 1 ? (tags.find((tag) => tag.id === tagIds[0])?.name ?? '1 tag') : `${tagIds.length} tags`}</span>
					<Icon name="chevron-down" className="h-3.5 w-3.5" />
				</button>
			{/snippet}
			{#snippet panel({ activeId, optionId, listboxId })}
				<div id={listboxId} class="max-h-60 overflow-y-auto py-1" role="listbox" aria-label="Tags" aria-multiselectable="true">
					{#each visibleTags as tag (tag.id)}
						<button
							type="button"
							id={optionId(tag.id)}
							role="option"
							aria-selected={tagIds.includes(tag.id)}
							class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-surface-3/50 {tagIds.includes(tag.id)
								? 'bg-surface-2'
								: ''} {activeId === tag.id ? 'ring-1 ring-inset ring-signal/50' : ''}"
							onclick={() => toggleTag(tag.id)}
						>
							<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background:{tag.color}"></span>
							<span class="min-w-0 flex-1 truncate text-fg">{tag.name}</span>
							{#if tagIds.includes(tag.id)}<Icon name="check" className="h-4 w-4 text-signal" />{/if}
						</button>
					{:else}
						<p class="px-3 py-3 text-sm text-fg-subtle">No tags match.</p>
					{/each}
				</div>
			{/snippet}
		</SearchableMultiSelectPopover>
	{/if}

	{#if showFavorites && current}
		<IconButton
			icon="star"
			label="Favorites only"
			size="sm"
			active={current.favorites}
			ariaPressed={current.favorites}
			class="flex-shrink-0 {current.favorites ? '' : 'bg-surface-2'}"
			data-favorites-filter
			onclick={setFavorites}
		/>
	{/if}

	{#if showFilters}
		<PickerPopover label="Filters" bind:open={filtersOpen}>
			{#snippet trigger({ open, toggle })}
				<button
					type="button"
					class={pill(popoverCount > 0 || open)}
					aria-haspopup="dialog"
					aria-expanded={open}
					data-filters-trigger
					onclick={toggle}
				>
					<Icon name="sliders" className="h-4 w-4" />
					<span>Filters</span>
					{#if popoverCount > 0}<span class="font-mono tabular-nums" data-filters-count>{popoverCount}</span>{/if}
				</button>
			{/snippet}
			{#snippet children()}
				<PickerFiltersPanel
					{source}
					{filters}
					{kinds}
					{tier}
					{tags}
					{collections}
					{facets}
					{kitLabels}
					{activeCount}
					{onChange}
					{onClear}
				/>
			{/snippet}
		</PickerPopover>
	{/if}

	{#if activeCount > 0 && tier !== 'sm'}
		<IconButton icon="close" label="Clear filters" size="sm" class="flex-shrink-0 bg-surface-2" data-clear-filters onclick={onClear} />
	{/if}

	{#if showSort}
		<select
			aria-label="Sort"
			class="input !h-8 !min-h-8 !w-auto flex-shrink-0 !py-0 text-sm ml-auto"
			value={history.sort}
			onchange={(event) => setSort(event.currentTarget.value)}
		>
			{#each SORT_OPTIONS as option (option.id)}
				<option value={option.id}>{option.label}</option>
			{/each}
		</select>
	{/if}

	<TileSizeControl class={showSort ? '' : 'ml-auto'} />
</div>
