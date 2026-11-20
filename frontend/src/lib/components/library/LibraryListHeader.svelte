<script lang="ts">
	import { setContext, type Snippet } from 'svelte';
	import { Menu } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		HeaderAction,
		type HeaderStage
	} from '$lib/components/page-header';
	import { ACTIVE_FILTERS_KEY } from './activeFilters';
	import type { FilterChip, SortOption } from './librarySection';

	let {
		title,
		count = null,
		total = null,
		unit,
		unitOne,
		chips = [],
		onRemoveChip,
		onClearFilters,
		q,
		onQueryChange,
		searchPlaceholder,
		searchHint = null,
		searchInputEl = $bindable(undefined),
		popover,
		filterCount,
		sortOptions = [],
		sortBy = '',
		sortDir = 'asc',
		sortDirDisabled = false,
		onSortChange = () => {},
		onSortDirChange = () => {},
		onRefresh,
		primaryLabel,
		primaryIcon = 'plus',
		onPrimary,
		moreItems = [],
		onMore,
		sidebarSheet,
		sectionSelect,
		searchModifiers,
		collectionActions,
		showSearch = true
	}: {
		title: string;
		count?: number | null;
		total?: number | null;
		unit?: string;
		unitOne?: string;
		chips?: readonly FilterChip[];
		onRemoveChip?: (key: string) => void;
		onClearFilters?: () => void;
		q: string;
		onQueryChange: (value: string) => void;
		searchPlaceholder: string;
		searchHint?: string | null;
		searchInputEl?: HTMLInputElement;
		popover?: Snippet<[() => void]>;
		filterCount?: number;
		sortOptions?: readonly SortOption[];
		sortBy?: string;
		sortDir?: 'asc' | 'desc';
		sortDirDisabled?: boolean;
		onSortChange?: (value: string) => void;
		onSortDirChange?: (dir: 'asc' | 'desc') => void;
		onRefresh?: () => void;
		primaryLabel?: string;
		primaryIcon?: string;
		onPrimary?: () => void;
		moreItems?: MenuItem[];
		onMore?: (id: string) => void;
		sidebarSheet?: Snippet;
		sectionSelect?: Snippet;
		searchModifiers?: Snippet;
		collectionActions?: Snippet;
		showSearch?: boolean;
	} = $props();

	let stage = $state<HeaderStage>('full');
	let filtersOpen = $state(false);

	setContext(ACTIVE_FILTERS_KEY, {
		chips: () => chips,
		remove: (key: string) => onRemoveChip?.(key)
	});

	const badge = $derived(filterCount ?? chips.length);
	const sortItems = $derived(sortOptions.map((option) => ({ value: option.value, label: option.label })));

	const narrowMenu = $derived.by(() => {
		if (stage !== 'narrow') return [] as MenuItem[];
		const items: MenuItem[] = [];
		if (onRefresh) items.push({ id: 'ui-refresh', label: 'Refresh', icon: 'refresh' });
		if (sortItems.length > 0) {
			for (const option of sortItems) {
				items.push({
					id: `ui-sort-${option.value}`,
					label: `Sort by ${option.label.toLowerCase()}`,
					icon: sortBy === option.value ? 'check' : undefined
				});
			}
			if (!sortDirDisabled) {
				items.push({
					id: 'ui-sort-dir',
					label: sortDir === 'desc' ? 'Descending. Switch to ascending' : 'Ascending. Switch to descending',
					icon: sortDir === 'desc' ? 'arrow-down' : 'arrow-up'
				});
			}
		}
		if (items.length > 0 && moreItems.length > 0) items.push({ id: 'ui-separator', label: '', separator: true });
		return items;
	});

	const menuItems = $derived([...narrowMenu, ...moreItems]);

	function handleSelect(id: string) {
		if (id === 'ui-refresh') onRefresh?.();
		else if (id === 'ui-sort-dir') onSortDirChange(sortDir === 'desc' ? 'asc' : 'desc');
		else if (id.startsWith('ui-sort-')) onSortChange(id.slice('ui-sort-'.length));
		else onMore?.(id);
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === '/' && !isTypingInField(event)) {
			event.preventDefault();
			searchInputEl?.focus();
		}
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

{#snippet leadingZone()}
	{@render sidebarSheet?.()}
	{@render sectionSelect?.()}
{/snippet}

{#snippet titleZone(s: HeaderStage)}
	<HeaderTitle
		{title}
		count={count ?? undefined}
		{unit}
		{unitOne}
		{total}
		filterCount={chips.length}
		onOpenFilters={popover ? () => (filtersOpen = true) : undefined}
		showUnit={s === 'full'}
		narrow={s === 'narrow'}
	/>
{/snippet}

{#snippet searchZone(s: HeaderStage)}
	{#if showSearch}
		<HeaderSearch
			value={q}
			placeholder={searchPlaceholder}
			oninput={onQueryChange}
			bind:inputEl={searchInputEl}
			narrow={s === 'narrow'}
		>
			{#snippet modifiers()}
				{#if searchHint}
					<span class="whitespace-nowrap px-1 font-mono text-sm tabular-nums text-fg-subtle">{searchHint}</span>
				{/if}
				{@render searchModifiers?.()}
			{/snippet}
		</HeaderSearch>
	{/if}
{/snippet}

{#snippet filtersZone(s: HeaderStage)}
	{#if popover}
		<HeaderFilters
			bare
			bind:open={filtersOpen}
			activeCount={badge}
			onClear={() => onClearFilters?.()}
			iconOnly={s === 'narrow'}
		>
			{#snippet children(close)}
				{@render popover(close)}
			{/snippet}
		</HeaderFilters>
	{/if}
{/snippet}

{#snippet sortZone()}
	{#if sortItems.length > 0}
		<HeaderSort
			options={sortItems}
			value={sortBy}
			dir={sortDir}
			onchange={onSortChange}
			ondir={onSortDirChange}
			dirDisabled={sortDirDisabled}
		/>
	{/if}
{/snippet}

{#snippet viewZone()}
	{@render collectionActions?.()}
	{#if onRefresh}
		<HeaderViewOptions onrefresh={onRefresh} />
	{/if}
{/snippet}

{#snippet primaryZone(s: HeaderStage)}
	{#if primaryLabel && onPrimary}
		<HeaderAction primary icon={primaryIcon} label={primaryLabel} compact={s === 'narrow'} onclick={onPrimary} />
	{/if}
{/snippet}

{#snippet moreZone()}
	{#if menuItems.length > 0}
		<Menu label="More actions" tooltip="More actions" items={menuItems} onselect={handleSelect} width={220} />
	{/if}
{/snippet}

<ListPageHeader
	bind:stage
	leading={sidebarSheet || sectionSelect ? leadingZone : undefined}
	title={titleZone}
	search={searchZone}
	filters={filtersZone}
	sort={sortZone}
	view={viewZone}
	primary={primaryZone}
	more={moreZone}
/>
