<script lang="ts">
	import { mediaGroupByDay } from '$lib/stores/mediaGroupByDay';
	import tooltip from '$lib/actions/tooltip';
	import { onDestroy, onMount } from 'svelte';
	import { debounce } from '$lib/utils/time';
	import { goto } from '$app/navigation';
	import { historyStore } from '$lib/stores/history';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { buildImportBundleTabData, buildReuseTabTitle, resolveReusePresetLabel } from '$lib/utils/historyReuse';
	import { announceReuseConversion } from '$lib/utils/reuseNotice';
	import { loadPresetNameMap, loadPresets } from '$lib/stores/presetsCatalog';
	import { presetSections, engineLabel } from '$lib/components/preset/presetDropdownModel';
	import type { PresetInfo } from '$lib/types/api';
	import type { HeaderOption } from '$lib/components/page-header';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { ORIGIN_FILTER_OPTIONS, originFilterLabel } from '$lib/generation/origin';
	import type { HistoryOriginFilter } from '$lib/types/history';
	import { buildModelOptions, modelFilterLabel } from '$lib/utils/historyModelFilter';
	import { Menu, ModelPickTrigger } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderToggle,
		HeaderSegmented,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		HeaderDropdown,
		type HeaderStage,
		type FilterChip
	} from '$lib/components/page-header';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import HistorySearchableFilter from './HistorySearchableFilter.svelte';
	import HistorySidebar from './HistorySidebar.svelte';
	import CollectionSidebarSheet from '$lib/components/collections/CollectionSidebarSheet.svelte';
	import { excludesSubcollections } from '$lib/components/collections/subcollections';
	import IncludeSubcollections from '$lib/components/collections/IncludeSubcollections.svelte';
	import { historyCollectionsStore } from '$lib/stores/collections';
	import type { HistorySearchMode, SortBy, SortDir } from '$lib/types/history';
	import { hasConvertibleHistoryFilters, historyFilterRule } from '$lib/organize/filterPrefill';
	import { startRuleFrom } from '$lib/organize/handoff';

	export let onOpenUpload: () => void;
	export let onOpenAddTag: () => void;
	export let onOpenDeleteByCriteria: () => void;
	export let sidebarOpen = true;
	export let onOpenSidebar: () => void = () => {};

	let stage: HeaderStage = 'full';
	let filtersOpen = false;

	$: currentState = $historyStore;
	$: excludingSubcollections = excludesSubcollections(
		$historyCollectionsStore.collections,
		currentState.filters.collectionId,
		currentState.filters.directOnly
	);
	$: facetData = currentState.facets;

	let isModelPickerOpen = false;

	const sortFields: Array<{ value: string; label: string }> = [
		{ value: 'created_at', label: 'Created' },
		{ value: 'rating', label: 'Rating' },
		{ value: 'file_size', label: 'Size' }
	];

	$: sortField = currentState.filters.sortBy ?? 'created_at';
	$: sortDirection = currentState.filters.sortDir ?? 'desc';
	$: sortOptions =
		sortField === 'completed_at' ? [...sortFields, { value: 'completed_at', label: 'Completed' }] : sortFields;

	const minRatingOptions: Array<{ value: number; label: string }> = [
		{ value: 0, label: 'Any rating' },
		{ value: 5, label: '★ 5' },
		{ value: 4, label: '★ 4+' },
		{ value: 3, label: '★ 3+' },
		{ value: 2, label: '★ 2+' },
		{ value: 1, label: '★ 1+' }
	];

	const mediaTypeItems = [
		{ value: 'all', label: 'All', tooltip: 'All media' },
		{ value: 'image', icon: 'image', tooltip: 'Images only' },
		{ value: 'video', icon: 'video', tooltip: 'Videos only' },
		{ value: 'audio', icon: 'audio', tooltip: 'Audio only' },
		{ value: 'mesh', icon: 'cube', tooltip: '3D only' }
	];

	const mediaTypeLabels: Record<string, string> = {
		image: 'Images',
		video: 'Videos',
		audio: 'Audio',
		mesh: '3D'
	};

	const datePresets: Array<{ value: 'all' | 'today' | 'yesterday' | 'last_week' | 'last_month'; label: string }> = [
		{ value: 'all', label: 'All' },
		{ value: 'today', label: 'Today' },
		{ value: 'yesterday', label: 'Yesterday' },
		{ value: 'last_week', label: '7 Days' },
		{ value: 'last_month', label: '30 Days' }
	];

	const statusOptions: HeaderOption[] = [
		{ value: 'all', label: 'All Status' },
		{ value: 'completed', label: 'Completed' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'cancelled', label: 'Cancelled' },
		{ value: 'pending', label: 'Pending' },
		{ value: 'running', label: 'Running' }
	];
	const originOptions: HeaderOption[] = [
		{ value: '', label: 'All origins' },
		...ORIGIN_FILTER_OPTIONS.map((option) => ({ value: option.value, label: option.label }))
	];
	const ratingOptions: HeaderOption[] = minRatingOptions.map((option) => ({ value: String(option.value), label: option.label }));

	let presetCatalog: PresetInfo[] = [];

	function buildPresetOptions(facet: Array<{ id: string; name: string; count: number }>, catalog: PresetInfo[]): HeaderOption[] {
		const counts = new Map(facet.map((entry) => [entry.id, entry.count]));
		const present = catalog.filter((preset) => counts.has(preset.id));
		const known = new Set(present.map((preset) => preset.id));
		const options: HeaderOption[] = [{ value: '', label: 'All presets', pinned: true }];
		for (const section of presetSections(present, '', { pinned: [], recent: [] }, null)) {
			for (const preset of section.presets) {
				options.push({
					value: preset.id,
					label: preset.name,
					description: `${engineLabel(preset.engine)} · v${preset.version}`,
					count: counts.get(preset.id),
					group: section.label,
					groupIcon: section.icon,
					thumb: { id: preset.id, name: preset.name, cover: preset.media?.cover, category: preset.category }
				});
			}
		}
		for (const entry of facet) {
			if (!known.has(entry.id)) options.push({ value: entry.id, label: entry.name, count: entry.count, group: 'Other' });
		}
		return options;
	}

	$: presetOptions = buildPresetOptions(facetData.presets, presetCatalog);
	$: modeOptions = [
		{ value: '', label: 'All Modes' },
		...facetData.modes.map((m) => ({ value: m.value, label: modeLabel(m.value), count: m.count }))
	];

	const labelClass = 'block text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1';
	const selectClass = 'input text-sm py-1.5 px-2 bg-surface-3/50 w-full';
	const segClass = 'flex items-center gap-0.5 bg-surface-3/50 rounded p-0.5';

	function segButtonClass(active: boolean) {
		return `flex-1 px-2 py-1 text-sm rounded-sm transition-colors duration-100 ${
			active ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'
		}`;
	}

	async function handleFilterChange() {
		await historyStore.loadGenerations();
	}

	async function handleDatePresetChange(preset: 'all' | 'today' | 'yesterday' | 'last_week' | 'last_month') {
		historyStore.setDatePreset(preset);
		await historyStore.loadGenerations();
	}

	async function handleStatusChange(status: string) {
		historyStore.setFilter('status', status);
		await historyStore.loadGenerations();
	}

	async function handleMediaTypeChange(mediaType: string) {
		historyStore.setFilter('mediaType', mediaType as 'all' | 'image' | 'video' | 'audio' | 'mesh');
		await historyStore.loadGenerations();
	}

	const scheduleSearch = debounce(() => historyStore.loadGenerations(), 300);
	function handleSearchInput(value: string) {
		historyStore.setFilter('search', value);
		scheduleSearch();
	}

	async function handleSearchModeChange(mode: HistorySearchMode) {
		if (currentState.filters.searchMode === mode) return;
		const hasQuery = !!currentState.filters.search;
		historyStore.setFilter('searchMode', mode);
		if (hasQuery) {
			await historyStore.loadGenerations();
		}
	}

	onDestroy(scheduleSearch.cancel);

	function handleSortFieldChange(value: string) {
		historyStore.setSort(value as SortBy, sortDirection as SortDir);
		historyStore.loadGenerations();
	}

	function handleSortDirChange(dir: 'asc' | 'desc') {
		historyStore.setSort(sortField as SortBy, dir);
		historyStore.loadGenerations();
	}

	function handleFavoritesToggle() {
		historyStore.setFilter('favoritesOnly', !currentState.filters.favoritesOnly);
		historyStore.loadGenerations();
	}

	function handleMinRatingChange(value: number) {
		historyStore.setFilter('minRating', value || undefined);
		historyStore.loadGenerations();
	}

	function handleModeChange(value: string) {
		historyStore.setFilter('mode', value || undefined);
		historyStore.loadGenerations();
	}

	function handlePresetChange(value: string) {
		historyStore.setFilter('presetId', value || undefined);
		historyStore.loadGenerations();
	}

	function handleOriginChange(value: string) {
		historyStore.setOriginFilter((value || null) as HistoryOriginFilter | null);
		historyStore.loadGenerations();
	}

	function handleModelChange(value: string) {
		historyStore.setFilter('modelName', value || undefined);
		historyStore.loadGenerations();
	}

	function openModelPicker() {
		isModelPickerOpen = true;
	}

	let pickedModelLabels: Record<string, string> = {};

	$: modelOptions = buildModelOptions(facetData.models);
	$: modelFilterText = modelFilterLabel(currentState.filters.modelName ?? '', modelOptions, pickedModelLabels);

	function handleModelPicked(model: { filename?: string; name?: string } | null) {
		isModelPickerOpen = false;
		if (model?.filename && model.name) pickedModelLabels = { ...pickedModelLabels, [model.filename]: model.name };
		handleModelChange(model?.filename ?? '');
	}

	async function searchPhrasebookOptions(query: string) {
		const res = await api.searchPhrasebook(query, 30, 'all');
		const values = res.success && res.data ? (res.data.values ?? []) : [];
		return values.map((v: any) => ({
			id: v.id,
			label: v.label,
			sublabel: v.category_path || v.value
		}));
	}

	function handleUsedPhrasebookSelect(id: string | null, label: string | null) {
		historyStore.setFilter('usedPhrasebookValueId', id || undefined);
		historyStore.setFilter('usedPhrasebookLabel', label || undefined);
		historyStore.loadGenerations();
	}

	function handleSystemTagClear() {
		historyStore.setSystemTagFilter(null);
		historyStore.loadGenerations();
	}

	function handleClearAllFilters() {
		historyStore.clearFilters();
		handleFilterChange();
	}

	function handleDirectOnlyChange(value: boolean) {
		historyStore.setFilter('directOnly', value);
		handleFilterChange();
	}

	let importInput: HTMLInputElement;
	let importing = false;
	let presetNamesCache: Record<string, string> = {};

	onMount(() => {
		loadPresets()
			.then((response) => (presetCatalog = response.success ? (response.data ?? []) : []))
			.catch((err) => logger.error('Failed to load presets:', err));
		loadPresetNameMap()
			.then((map) => (presetNamesCache = map))
			.catch((err) => logger.error('Failed to load preset names:', err));
	});

	function resolvePresetName(id: string): string | null {
		return presetNamesCache[id] ?? null;
	}

	function triggerImportBundle() {
		importInput?.click();
	}

	async function handleImportBundleChange(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file || importing) return;

		importing = true;
		try {
			const response = await api.importGenerationBundle(file);
			if (!response.success || !response.data) {
				toasts.error(response.error || 'Import failed');
				return;
			}

			const { reuse, preset_available, warnings } = response.data;
			for (const warning of warnings) {
				toasts.warning(warning);
			}

			if (!preset_available) {
				toasts.error(
					`Preset "${resolveReusePresetLabel(reuse, resolvePresetName)}" is not installed — install it before reusing this bundle.`
				);
				return;
			}

			const tabName = buildReuseTabTitle('Imported', reuse, resolvePresetName);
			const imported = buildImportBundleTabData(reuse);
			tabsStore.addTabWithData(tabName, imported.tabData);
			announceReuseConversion(imported);
			goto('/generate');
		} catch (e) {
			logger.error('Import bundle failed:', e);
			toasts.error('Import failed. Please try again.');
		} finally {
			importing = false;
		}
	}

	$: canMakeRule = hasConvertibleHistoryFilters(currentState.filters);

	async function handleMakeRule() {
		const names = (ids: string[]) =>
			currentState.availableTags.filter((tag) => ids.includes(tag.id)).map((tag) => tag.name);
		const rule = await historyFilterRule(currentState.filters, { tagNames: names });
		await startRuleFrom(rule);
	}


	$: hasActiveFilters =
		currentState.filters.status !== 'all' ||
		currentState.filters.datePreset !== 'all' ||
		currentState.filters.selectedTagIds.length > 0 ||
		currentState.filters.mediaType !== 'all' ||
		currentState.filters.search !== '' ||
		!!currentState.filters.favoritesOnly ||
		!!currentState.filters.minRating ||
		!!currentState.filters.mode ||
		!!currentState.filters.presetId ||
		!!currentState.filters.modelName ||
		!!currentState.filters.origin ||
		!!currentState.filters.usedPhrasebookValueId ||
		excludingSubcollections;

	let unfilteredTotal: number | null = null;
	$: if (!hasActiveFilters && !currentState.loading) unfilteredTotal = currentState.totalCount;

	function buildChips(
		f: typeof currentState.filters,
		presetOptions: typeof facetData.presets,
		modelText: string,
		presetNames: Record<string, string>,
		excluding: boolean
	): FilterChip[] {
		const list: FilterChip[] = [];
		if (f.mediaType !== 'all') {
			list.push({ id: 'media', label: mediaTypeLabels[f.mediaType] ?? f.mediaType, onremove: () => handleMediaTypeChange('all') });
		}
		if (f.favoritesOnly) list.push({ id: 'favorites', label: 'Favorites', onremove: handleFavoritesToggle });
		if (f.presetId) {
			const name = presetOptions.find((p) => p.id === f.presetId)?.name ?? presetNames[f.presetId] ?? 'Preset';
			list.push({ id: 'preset', label: name, onremove: () => handlePresetChange('') });
		}
		if (f.datePreset !== 'all') {
			const label = datePresets.find((p) => p.value === f.datePreset)?.label ?? f.datePreset;
			list.push({ id: 'date', label, onremove: () => handleDatePresetChange('all') });
		}
		if (f.status !== 'all') {
			list.push({
				id: 'status',
				label: f.status.charAt(0).toUpperCase() + f.status.slice(1),
				onremove: () => handleStatusChange('all')
			});
		}
		if (f.mode) list.push({ id: 'mode', label: modeLabel(f.mode), onremove: () => handleModeChange('') });
		if (f.modelName) list.push({ id: 'model', label: modelText, onremove: () => handleModelChange('') });
		if (f.origin) list.push({ id: 'origin', label: originFilterLabel(f.origin), onremove: () => handleOriginChange('') });
		if (f.minRating) list.push({ id: 'rating', label: `★${f.minRating}+`, onremove: () => handleMinRatingChange(0) });
		if (f.usedPhrasebookValueId) {
			list.push({
				id: 'phrasebook',
				label: f.usedPhrasebookLabel ?? 'Phrasebook value',
				onremove: () => handleUsedPhrasebookSelect(null, null)
			});
		}
		if (f.systemTag) {
			list.push({ id: 'system-tag', label: f.systemTag.replace(/_/g, ' '), onremove: handleSystemTagClear });
		}
		if (excluding) {
			list.push({ id: 'subcollections', label: 'Without subcollections', onremove: () => handleDirectOnlyChange(false) });
		}
		return list;
	}

	$: chips = buildChips(currentState.filters, facetData.presets, modelFilterText, presetNamesCache, excludingSubcollections);

	$: moreItems = [
		...(stage === 'narrow'
			? [
					{ id: 'refresh', label: 'Refresh', icon: 'refresh' },
					{ id: 'size-small', label: 'Small tiles', icon: $historyTileSize === 'small' ? 'check' : undefined },
					{ id: 'size-medium', label: 'Medium tiles', icon: $historyTileSize === 'medium' ? 'check' : undefined },
					{ id: 'size-large', label: 'Large tiles', icon: $historyTileSize === 'large' ? 'check' : undefined },
					{ id: 'group', label: $mediaGroupByDay ? 'One continuous wall' : 'Group by date', icon: 'calendar' },
					{ id: 'view-separator', label: '', separator: true }
				]
			: []),
		{ id: 'upload', label: 'Upload generations', icon: 'upload' },
		{ id: 'import', label: importing ? 'Importing…' : 'Import bundle', icon: 'box', disabled: importing },
		{ id: 'tag', label: 'Add tag', icon: 'plus' },
		{ id: 'delete-separator', label: '', separator: true },
		{ id: 'delete', label: 'Delete by criteria', icon: 'trash', danger: true }
	];

	function handleMoreSelect(id: string) {
		if (id === 'refresh') historyStore.loadGenerations();
		else if (id.startsWith('size-')) historyTileSize.set(id.slice(5) as HistoryTileSize);
		else if (id === 'group') mediaGroupByDay.update((value) => !value);
		else if (id === 'upload') onOpenUpload();
		else if (id === 'import') triggerImportBundle();
		else if (id === 'tag') onOpenAddTag();
		else if (id === 'delete') onOpenDeleteByCriteria();
	}
</script>

{#if isModelPickerOpen}
	<ModelAssignmentModal
		selectionMode="single"
		allowClear={true}
		title="Filter history by model"
		subtitle="Search the model catalog or narrow it by type, then select one model."
		onSelect={handleModelPicked}
		onClear={() => handleModelPicked(null)}
		onClose={() => (isModelPickerOpen = false)}
	/>
{/if}

<input
	type="file"
	accept=".zip,.json"
	class="hidden"
	bind:this={importInput}
	on:change={handleImportBundleChange}
/>

<ListPageHeader bind:stage>
	{#snippet leading()}
		<CollectionSidebarSheet label="History" collapsed={!sidebarOpen} onExpand={onOpenSidebar}>
			<HistorySidebar />
		</CollectionSidebarSheet>
	{/snippet}

	{#snippet title(s)}
		<HeaderTitle
			title="History"
			count={currentState.totalCount}
			unit="generations"
			unitOne="generation"
			total={unfilteredTotal}
			filterCount={chips.length}
			onOpenFilters={() => (filtersOpen = true)}
			showUnit={s === 'full'}
			narrow={s === 'narrow'}
		/>
	{/snippet}

	{#snippet search(s)}
		<HeaderSearch
			value={currentState.filters.search}
			placeholder={currentState.filters.searchMode === 'semantic'
				? 'Describe what the image shows...'
				: 'Search generations...'}
			label="Search generations"
			oninput={handleSearchInput}
			narrow={s === 'narrow'}
		>
			{#snippet modifiers()}
				<HeaderToggle
					icon="sparkles"
					label="Semantic search"
					tooltip={currentState.filters.searchMode === 'semantic'
						? 'Semantic search on. Click for keyword search'
						: 'Semantic search: describe what the image shows'}
					pressed={currentState.filters.searchMode === 'semantic'}
					onclick={() =>
						handleSearchModeChange(currentState.filters.searchMode === 'semantic' ? 'keyword' : 'semantic')}
				/>
			{/snippet}
		</HeaderSearch>
	{/snippet}

	{#snippet facets(s)}
		{#if s !== 'tight' && s !== 'narrow'}
			<HeaderSegmented
				label="Media type"
				items={mediaTypeItems}
				value={currentState.filters.mediaType}
				onchange={handleMediaTypeChange}
			/>
		{/if}
		{#if s === 'full'}
			<button
				type="button"
				class="inline-flex h-8 w-8 flex-none items-center justify-center rounded border transition-colors duration-100 {currentState
					.filters.favoritesOnly
					? 'border-signal/25 bg-signal/10 text-signal'
					: 'border-line-strong bg-surface-2/50 text-fg-muted hover:bg-surface-3/60 hover:text-fg'}"
				use:tooltip={'Favorites only'}
				aria-label="Favorites only"
				aria-pressed={!!currentState.filters.favoritesOnly}
				on:click={handleFavoritesToggle}
			>
				<Icon name="heart" className="w-3.5 h-3.5" />
			</button>

			{#if facetData.presets.length > 0}
				<HeaderDropdown
					ariaLabel="Preset"
					active={!!currentState.filters.presetId}
					value={currentState.filters.presetId ?? ''}
					options={presetOptions}
					onchange={handlePresetChange}
				/>
			{/if}
		{/if}
	{/snippet}

	{#snippet filters(s)}
		<HeaderFilters
			bind:open={filtersOpen}
			activeCount={chips.length}
			{chips}
			onClear={handleClearAllFilters}
			iconOnly={s === 'narrow'}
		>
			{#snippet children(close)}
				{#if s === 'narrow'}
					<div class="col-span-2">
						<span class={labelClass}>Sort</span>
						<HeaderSort
							options={sortOptions}
							value={sortField}
							dir={sortDirection}
							onchange={handleSortFieldChange}
							ondir={handleSortDirChange}
						/>
					</div>
				{/if}

				{#if s === 'tight' || s === 'narrow'}
					<div class="col-span-2">
						<span class={labelClass}>Media type</span>
						<HeaderSegmented
							label="Media type"
							items={mediaTypeItems}
							value={currentState.filters.mediaType}
							onchange={handleMediaTypeChange}
						/>
					</div>
				{/if}

				{#if s !== 'full'}
					<div class={facetData.presets.length > 0 ? '' : 'col-span-2'}>
						<span class={labelClass}>Favorites</span>
						<button
							type="button"
							class="{selectClass} flex items-center gap-1.5 text-left {currentState.filters.favoritesOnly
								? 'text-signal'
								: 'text-fg-muted'}"
							aria-pressed={!!currentState.filters.favoritesOnly}
							on:click={handleFavoritesToggle}
						>
							<Icon name="heart" className="w-3.5 h-3.5" />
							Favorites only
						</button>
					</div>
					{#if facetData.presets.length > 0}
						<div>
							<span class={labelClass}>Preset</span>
							<HeaderDropdown block ariaLabel="Preset" value={currentState.filters.presetId ?? ''} options={presetOptions} onchange={handlePresetChange} />
						</div>
					{/if}
				{/if}

				<div class="col-span-2">
					<span class={labelClass}>Date</span>
					<div class={segClass}>
						{#each datePresets as preset}
							<button
								class={segButtonClass(currentState.filters.datePreset === preset.value)}
								on:click={() => handleDatePresetChange(preset.value)}
							>
								{preset.label}
							</button>
						{/each}
					</div>
				</div>

				<div>
					<span class={labelClass}>Status</span>
					<HeaderDropdown block ariaLabel="Status" value={currentState.filters.status} options={statusOptions} onchange={handleStatusChange} />
				</div>

				{#if facetData.modes.length > 0}
					<div>
						<span class={labelClass}>Mode</span>
						<HeaderDropdown block ariaLabel="Mode" value={currentState.filters.mode ?? ''} options={modeOptions} onchange={handleModeChange} />
					</div>
				{/if}

				<div class={facetData.modes.length > 0 ? '' : 'col-span-2'}>
					<span class={labelClass}>Model</span>
					<ModelPickTrigger
						id="history-filter-model"
						class="text-sm py-1.5 px-2 bg-surface-3/50"
						value={currentState.filters.modelName ? modelFilterText : null}
						placeholder="All Models"
						onopen={() => {
							close();
							openModelPicker();
						}}
						onclear={() => handleModelChange('')}
					/>
				</div>

				<div>
					<span class={labelClass}>Origin</span>
					<HeaderDropdown block ariaLabel="Origin" value={currentState.filters.origin ?? ''} options={originOptions} onchange={handleOriginChange} />
				</div>

				<div>
					<span class={labelClass}>Min rating</span>
					<HeaderDropdown
						block
						ariaLabel="Min rating"
						value={String(currentState.filters.minRating ?? 0)}
						options={ratingOptions}
						onchange={(value) => handleMinRatingChange(parseInt(value))}
					/>
				</div>

				<div class="col-span-2">
					<span class={labelClass}>Phrasebook used</span>
					<HistorySearchableFilter
						placeholder="Any value"
						icon="tag"
						selectedId={currentState.filters.usedPhrasebookValueId}
						selectedLabel={currentState.filters.usedPhrasebookLabel}
						search={searchPhrasebookOptions}
						onSelect={handleUsedPhrasebookSelect}
					/>
				</div>

				<IncludeSubcollections
					collections={$historyCollectionsStore.collections}
					activeId={currentState.filters.collectionId}
					directOnly={!!currentState.filters.directOnly}
					onDirectOnlyChange={handleDirectOnlyChange}
				/>
			{/snippet}
			{#snippet footerAction(close)}
				{#if canMakeRule}
					<button
						class="text-sm text-fg-muted hover:text-fg transition-colors flex items-center gap-1"
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
	{/snippet}

	{#snippet sort()}
		<HeaderSort
			options={sortOptions}
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
		<HistorySidebar part="actions" />
		<HeaderViewOptions onrefresh={() => historyStore.loadGenerations()} />
	{/snippet}

	{#snippet more()}
		<Menu label="More actions" tooltip="More actions" items={moreItems} onselect={handleMoreSelect} width={200} />
	{/snippet}
</ListPageHeader>
