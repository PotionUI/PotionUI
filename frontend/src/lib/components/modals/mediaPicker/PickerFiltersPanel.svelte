<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, SegmentedControl } from '$lib/components/ui';
	import { buildTree, flattenTree } from '$lib/components/pane';
	import type { HistoryFacets, Tag } from '$lib/types/history';
	import type { PickerMediaType } from '../mediaPickerSelection';
	import {
		DATE_PRESETS,
		SORT_OPTIONS,
		type HistorySort,
		type PickerFilters,
		type PickerSourceId,
		type PickerTier
	} from './pickerModel';
	import { kindFilterItems } from './pickerSelection';

	type CollectionNode = { id: string; name: string; parent_id?: string | null };

	let {
		source,
		filters,
		kinds,
		tier,
		tags,
		collections,
		facets,
		kitLabels,
		activeCount,
		onChange,
		onClear
	}: {
		source: PickerSourceId;
		filters: PickerFilters;
		kinds: readonly PickerMediaType[];
		tier: PickerTier;
		tags: Tag[];
		collections: CollectionNode[];
		facets: HistoryFacets;
		kitLabels: string[];
		activeCount: number;
		onChange: (next: PickerFilters) => void;
		onClear: () => void;
	} = $props();

	let history = $derived(filters.history);
	let library = $derived(filters.library);
	let current = $derived(source === 'history' ? history : source === 'library' ? library : null);
	let nodes = $derived(flattenTree(buildTree(collections as never[])) as Array<{ item: CollectionNode; depth: number }>);
	let selectedCollection = $derived(
		source === 'library' ? (library.unsorted ? 'unsorted' : (library.collectionId ?? '')) : (history.collectionId ?? '')
	);
	let tagIds = $derived(current?.tagIds ?? []);
	let showCustomDates = $state(false);

	function patchHistory(patch: Partial<PickerFilters['history']>) {
		onChange({ ...filters, history: { ...history, ...patch } });
	}

	function patchLibrary(patch: Partial<PickerFilters['library']>) {
		onChange({ ...filters, library: { ...library, ...patch } });
	}

	function patchKits(patch: Partial<PickerFilters['kits']>) {
		onChange({ ...filters, kits: { ...filters.kits, ...patch } });
	}

	function setCollection(value: string) {
		if (source === 'library') {
			patchLibrary(value === 'unsorted' ? { unsorted: true, collectionId: null } : { unsorted: false, collectionId: value || null });
		} else {
			patchHistory({ collectionId: value || null });
		}
	}

	function toggleTag(id: string) {
		const next = tagIds.includes(id) ? tagIds.filter((tag) => tag !== id) : [...tagIds, id];
		if (source === 'library') patchLibrary({ tagIds: next });
		else patchHistory({ tagIds: next });
	}

	function setKind(id: string) {
		const kind = id as PickerFilters['history']['kind'];
		if (source === 'library') patchLibrary({ kind });
		else patchHistory({ kind });
	}

	function setFavorites(value: boolean) {
		if (source === 'library') patchLibrary({ favorites: value });
		else patchHistory({ favorites: value });
	}

	function setDate(id: string) {
		if (id === 'custom') {
			showCustomDates = true;
			patchHistory({ date: 'custom' });
			return;
		}
		showCustomDates = false;
		patchHistory({ date: id as PickerFilters['history']['date'], dateFrom: '', dateTo: '' });
	}

	const sectionLabel = 'mb-1.5 block text-sm font-medium text-fg-muted';
	const chipClass = (on: boolean) =>
		`inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-sm font-medium transition-colors ${on ? 'bg-signal/10 text-signal' : 'bg-surface-3 text-fg-muted hover:text-fg'}`;
</script>

<div class="flex items-center justify-between border-b border-line px-3 py-2">
	<span class="text-sm font-semibold text-fg">Filters</span>
	<Button variant="ghost" size="xs" icon="close" disabled={activeCount === 0} onclick={onClear} attrs={{ 'data-clear-all': '' }}>
		Clear all
	</Button>
</div>

<div class="max-h-[22rem] space-y-3 overflow-y-auto p-3" data-filters-body>
	{#if tier === 'sm' && source !== 'kits' && kinds.length > 1 && current}
		<div>
			<span class={sectionLabel}>Kind</span>
			<SegmentedControl
				variant="toggle"
				fullWidth
				ariaLabel="Kind"
				items={kindFilterItems(kinds)}
				selected={current.kind}
				onSelect={setKind}
			/>
		</div>
	{/if}

	{#if tier === 'sm' && source === 'kits' && kitLabels.length > 0}
		<div>
			<span class={sectionLabel}>Label</span>
			<SegmentedControl
				variant="toggle"
				fullWidth
				ariaLabel="Label"
				items={[{ id: '', label: 'All' }, ...kitLabels.map((label) => ({ id: label, label }))]}
				selected={filters.kits.label ?? ''}
				onSelect={(id) => patchKits({ label: id || null })}
			/>
		</div>
	{/if}

	{#if source !== 'kits' && tier !== 'xl'}
		<div>
			<label class={sectionLabel} for="pk-filter-collection">Collection</label>
			<select
				id="pk-filter-collection"
				class="input !h-8 !min-h-8 !py-0 text-sm"
				value={selectedCollection}
				onchange={(event) => setCollection(event.currentTarget.value)}
			>
				<option value="">{source === 'library' ? 'All items' : 'All collections'}</option>
				{#if source === 'library'}<option value="unsorted">Unsorted</option>{/if}
				{#each nodes as node (node.item.id)}
					<option value={node.item.id}>{'  '.repeat(node.depth)}{node.item.name}</option>
				{/each}
			</select>
		</div>
		<div>
			<span class={sectionLabel}>Tags</span>
			<div class="flex flex-wrap gap-1.5">
				{#each tags as tag (tag.id)}
					<button
						type="button"
						aria-pressed={tagIds.includes(tag.id)}
						class={chipClass(tagIds.includes(tag.id))}
						data-filter-tag={tag.id}
						onclick={() => toggleTag(tag.id)}
					>
						<span class="h-2 w-2 rounded-full" style="background:{tag.color}"></span>{tag.name}
					</button>
				{:else}
					<p class="text-sm text-fg-subtle">No tags yet.</p>
				{/each}
			</div>
		</div>
	{/if}

	{#if source !== 'kits' && tier === 'sm'}
		<label class="flex cursor-pointer items-center gap-2 text-sm text-fg">
			<input
				type="checkbox"
				class="h-4 w-4 accent-signal"
				checked={current?.favorites ?? false}
				onchange={(event) => setFavorites(event.currentTarget.checked)}
			/>
			Favorites only
		</label>
	{/if}

	{#if source === 'history'}
		<div>
			<span class={sectionLabel}>Date</span>
			<div class="flex flex-wrap gap-1.5">
				{#each DATE_PRESETS as preset (preset.id)}
					<button
						type="button"
						aria-pressed={history.date === preset.id}
						class={chipClass(history.date === preset.id)}
						data-date={preset.id}
						onclick={() => setDate(preset.id)}
					>
						{preset.label}
					</button>
				{/each}
				<button
					type="button"
					aria-pressed={history.date === 'custom'}
					class={chipClass(history.date === 'custom')}
					data-date="custom"
					onclick={() => setDate('custom')}
				>
					<Icon name="calendar" className="h-3.5 w-3.5" />Custom range
				</button>
			</div>
			{#if history.date === 'custom' || showCustomDates}
				<div class="mt-2 grid grid-cols-2 gap-2">
					<label class="block text-sm text-fg-muted">
						From
						<input
							type="date"
							class="input mt-1 w-full text-sm"
							value={history.dateFrom}
							onchange={(event) => patchHistory({ dateFrom: event.currentTarget.value, date: 'custom' })}
						/>
					</label>
					<label class="block text-sm text-fg-muted">
						To
						<input
							type="date"
							class="input mt-1 w-full text-sm"
							value={history.dateTo}
							onchange={(event) => patchHistory({ dateTo: event.currentTarget.value, date: 'custom' })}
						/>
					</label>
				</div>
			{/if}
		</div>
		<div class="grid grid-cols-2 gap-2">
			<div>
				<label class={sectionLabel} for="pk-filter-model">Model</label>
				<select
					id="pk-filter-model"
					class="input !h-8 !min-h-8 !py-0 text-sm"
					value={history.model}
					onchange={(event) => patchHistory({ model: event.currentTarget.value })}
				>
					<option value="">Any</option>
					{#each facets.models as model (model.name)}
						<option value={model.name}>{model.name}</option>
					{/each}
				</select>
			</div>
			<div>
				<label class={sectionLabel} for="pk-filter-preset">Preset</label>
				<select
					id="pk-filter-preset"
					class="input !h-8 !min-h-8 !py-0 text-sm"
					value={history.presetId}
					onchange={(event) => patchHistory({ presetId: event.currentTarget.value })}
				>
					<option value="">Any</option>
					{#each facets.presets as preset (preset.id)}
						<option value={preset.id}>{preset.name}</option>
					{/each}
				</select>
			</div>
		</div>
		{#if tier === 'sm'}
			<div>
				<label class={sectionLabel} for="pk-filter-sort">Sort</label>
				<select
					id="pk-filter-sort"
					class="input !h-8 !min-h-8 !py-0 text-sm"
					value={history.sort}
					onchange={(event) => patchHistory({ sort: event.currentTarget.value as HistorySort })}
				>
					{#each SORT_OPTIONS as option (option.id)}
						<option value={option.id}>{option.label}</option>
					{/each}
				</select>
			</div>
		{/if}
	{/if}
</div>
