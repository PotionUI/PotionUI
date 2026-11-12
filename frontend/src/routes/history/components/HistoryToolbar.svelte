<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { onDestroy, onMount } from 'svelte';
	import { debounce } from '$lib/utils/time';
	import { goto } from '$app/navigation';
	import dismiss, { type DismissReason } from '$lib/actions/dismiss';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { historyStore } from '$lib/stores/history';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { buildImportBundleTabData, buildReuseTabTitle, resolveReusePresetLabel } from '$lib/utils/historyReuse';
	import { announceReuseConversion } from '$lib/utils/reuseNotice';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { PageHeader, PageTitle, IconButton, ModelPickTrigger } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { api } from '$lib/services/api/index';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import HistorySearchableFilter from './HistorySearchableFilter.svelte';
	import HistorySidebar from './HistorySidebar.svelte';
	import CollectionSidebarSheet from '$lib/components/collections/CollectionSidebarSheet.svelte';
	import { excludesSubcollections } from '$lib/components/collections/subcollections';
	import IncludeSubcollections from '$lib/components/collections/IncludeSubcollections.svelte';
	import FiltersPopover from '$lib/components/collections/FiltersPopover.svelte';
	import { historyCollectionsStore } from '$lib/stores/collections';
	import type { HistorySearchMode, SortBy, SortDir } from '$lib/types/history';
	import { computeAnchoredMenuPosition } from '$lib/utils/menuPosition';
	import { hasConvertibleHistoryFilters, historyFilterRule } from '$lib/organize/filterPrefill';
	import { startRuleFrom } from '$lib/organize/handoff';

	// Self-contained: reads/writes historyStore directly. Modal-opening
	// callbacks stay as props since modal state lives on the page.
	export let onOpenUpload: () => void;
	export let onOpenAddTag: () => void;
	export let onOpenDeleteByCriteria: () => void;
	export let sidebarOpen = true;
	export let onOpenSidebar: () => void = () => {};

	$: currentState = $historyStore;
	$: excludingSubcollections = excludesSubcollections(
		$historyCollectionsStore.collections,
		currentState.filters.collectionId,
		currentState.filters.directOnly
	);
	$: facets = currentState.facets;

	let isMoreMenuOpen = false;
	let isModelPickerOpen = false;
	let moreMenuButtonEl: HTMLDivElement;
	let moreMenuPanelEl: HTMLDivElement;
	let moreMenuPosition = { top: 0, left: 0 };

	function updateMoreMenuPosition() {
		if (!moreMenuButtonEl) return;
		const panelWidth = moreMenuPanelEl?.getBoundingClientRect().width ?? 180;
		moreMenuPosition = computeAnchoredMenuPosition(moreMenuButtonEl, { width: panelWidth, align: 'right' });
	}

	function toggleMoreMenu() {
		isMoreMenuOpen = !isMoreMenuOpen;
		if (isMoreMenuOpen) {
			updateMoreMenuPosition();
			requestAnimationFrame(updateMoreMenuPosition);
		}
	}

	function closeMoreMenu() {
		isMoreMenuOpen = false;
	}

	// Sort options map to a sort_by + sort_dir pair, encoded as "field:dir".
	const sortOptions: Array<{ value: string; label: string }> = [
		{ value: 'created_at:desc', label: 'Newest' },
		{ value: 'created_at:asc', label: 'Oldest' },
		{ value: 'rating:desc', label: 'Highest rated' },
		{ value: 'rating:asc', label: 'Lowest rated' },
		{ value: 'file_size:desc', label: 'Largest first' }
	];

	const minRatingOptions: Array<{ value: number; label: string }> = [
		{ value: 0, label: 'Any rating' },
		{ value: 5, label: '★ 5' },
		{ value: 4, label: '★ 4+' },
		{ value: 3, label: '★ 3+' },
		{ value: 2, label: '★ 2+' },
		{ value: 1, label: '★ 1+' }
	];

	$: currentSortValue = `${currentState.filters.sortBy ?? 'created_at'}:${currentState.filters.sortDir ?? 'desc'}`;

	const tileSizes: Array<{ value: HistoryTileSize; label: string; title: string }> = [
		{ value: 'small', label: 'S', title: 'Small tiles' },
		{ value: 'medium', label: 'M', title: 'Medium tiles' },
		{ value: 'large', label: 'L', title: 'Large tiles' }
	];

	const datePresets: Array<{ value: 'all' | 'today' | 'yesterday' | 'last_week' | 'last_month'; label: string }> = [
		{ value: 'all', label: 'All' },
		{ value: 'today', label: 'Today' },
		{ value: 'yesterday', label: 'Yesterday' },
		{ value: 'last_week', label: '7 Days' },
		{ value: 'last_month', label: '30 Days' }
	];

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

	async function handleMediaTypeChange(mediaType: 'all' | 'image' | 'video' | 'audio' | 'mesh') {
		historyStore.setFilter('mediaType', mediaType);
		await historyStore.loadGenerations();
	}

	// Search drives server-side full-text or semantic visual search, debounced.
	const scheduleSearch = debounce(() => historyStore.loadGenerations(), 300);
	function handleSearchInput(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		historyStore.setFilter('search', value);
		scheduleSearch();
	}

	const searchModes: Array<{ value: HistorySearchMode; label: string; title: string }> = [
		{ value: 'keyword', label: 'Keyword', title: 'Match prompt, preset and model text' },
		{ value: 'semantic', label: 'Semantic', title: 'Describe what the image shows' }
	];

	async function handleSearchModeChange(mode: HistorySearchMode) {
		if (currentState.filters.searchMode === mode) return;
		const hasQuery = !!currentState.filters.search;
		historyStore.setFilter('searchMode', mode);
		if (hasQuery) {
			await historyStore.loadGenerations();
		}
	}

	onDestroy(scheduleSearch.cancel);

	function handleSortChange(value: string) {
		const [sortBy, sortDir] = value.split(':') as [SortBy, SortDir];
		historyStore.setSort(sortBy, sortDir);
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

	function handleModelChange(value: string) {
		historyStore.setFilter('modelName', value || undefined);
		historyStore.loadGenerations();
	}

	function openModelPicker() {
		isModelPickerOpen = true;
	}

	function handleModelPicked(model: { filename?: string } | null) {
		isModelPickerOpen = false;
		handleModelChange(model?.filename ?? '');
	}

	// Search phrasebook values (navigational by category path) for the
	// "Phrasebook used" filter.
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

	function dismissFrom(selector: string, close: () => void) {
		return (reason: DismissReason, event: Event) => {
			if (reason === 'outside' && event.target instanceof Element && event.target.closest(selector)) return;
			close();
		};
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
		!!currentState.filters.usedPhrasebookValueId ||
		excludingSubcollections;

	// Filters tucked into the "Filters" popover — surfaced via the trigger's
	// badge count and the active-filter chip row so their state is never hidden.
	$: advancedActiveCount =
		(currentState.filters.searchMode === 'semantic' ? 1 : 0) +
		(currentState.filters.datePreset !== 'all' ? 1 : 0) +
		(currentState.filters.status !== 'all' ? 1 : 0) +
		(currentState.filters.mode ? 1 : 0) +
		(currentState.filters.modelName ? 1 : 0) +
		(currentState.filters.minRating ? 1 : 0) +
		(currentState.filters.usedPhrasebookValueId ? 1 : 0) +
		(currentState.filters.systemTag ? 1 : 0) +
		(excludingSubcollections ? 1 : 0);
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

<PageHeader wrap sticky={false}>
	<div class="flex flex-col gap-2 w-full">
		<div class="flex flex-wrap items-center gap-2 xl:gap-4 w-full">
			<CollectionSidebarSheet label="History" collapsed={!sidebarOpen} onExpand={onOpenSidebar}>
				<HistorySidebar />
			</CollectionSidebarSheet>
			<PageTitle title="History" count={currentState.totalCount} countLabel="generations" countLabelOne="generation" />

			<!-- Divider -->
			<div class="hidden md:block h-6 w-px bg-line-strong flex-shrink-0"></div>

			<div class="order-last xl:order-none flex items-center gap-1.5 w-full xl:w-auto xl:flex-1 xl:min-w-[12rem] xl:max-w-md">
				<div class="relative flex-1 min-w-[8rem]">
					<Icon
						name="search"
						className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
					/>
					<input
						type="text"
						class="input text-xs py-1.5 pl-8 pr-3 bg-surface-2/50 w-full"
						placeholder={currentState.filters.searchMode === 'semantic'
							? 'Describe what the image shows...'
							: 'Search generations...'}
						value={currentState.filters.search}
						on:input={handleSearchInput}
					/>
				</div>
			</div>

			<!-- High-frequency filters: media type, favorites, preset — the rest live in "Filters" -->
			<div class="hidden md:flex items-center gap-2 flex-shrink-0">
				<!-- Media type -->
				<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5">
					<button
						class="px-2 py-1 text-xs rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'all'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						on:click={() => handleMediaTypeChange('all')}
					>
						All
					</button>
					<button
						class="p-1.5 rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'image'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						use:tooltip={'Images only'}
						aria-label="Images only"
						on:click={() => handleMediaTypeChange('image')}
					>
						<Icon name="image" className="w-3.5 h-3.5" />
					</button>
					<button
						class="p-1.5 rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'video'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						use:tooltip={'Videos only'}
						aria-label="Videos only"
						on:click={() => handleMediaTypeChange('video')}
					>
						<Icon name="video" className="w-3.5 h-3.5" />
					</button>
					<button
						class="p-1.5 rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'audio'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						use:tooltip={'Audio only'}
						aria-label="Audio only"
						on:click={() => handleMediaTypeChange('audio')}
					>
						<Icon name="audio" className="w-3.5 h-3.5" />
					</button>
					<button
						class="p-1.5 rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'mesh'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						use:tooltip={'3D only'}
						aria-label="3D only"
						on:click={() => handleMediaTypeChange('mesh')}
					>
						<Icon name="cube" className="w-3.5 h-3.5" />
					</button>
				</div>

				<!-- Favorites -->
				<button
					class="p-1.5 rounded transition-colors duration-100 bg-surface-2/50 {currentState.filters
						.favoritesOnly
						? 'text-signal'
						: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
					use:tooltip={'Favorites only'}
					aria-label="Favorites only"
					aria-pressed={!!currentState.filters.favoritesOnly}
					on:click={handleFavoritesToggle}
				>
					<Icon name="heart" className="w-3.5 h-3.5" />
				</button>

				<!-- Preset - PotionUI is preset-driven, so this is the primary way people slice their history -->
				{#if facets.presets.length > 0}
					<select
						class="input text-xs py-1.5 px-2 bg-surface-2/50 w-auto max-w-[9rem]"
						value={currentState.filters.presetId ?? ''}
						on:change={(e) => handlePresetChange(e.currentTarget.value)}
					>
						<option value="">All Presets</option>
						{#each facets.presets as p}
							<option value={p.id}>{p.name} ({p.count})</option>
						{/each}
					</select>
				{/if}

				<FiltersPopover
					activeCount={advancedActiveCount}
					canClear={hasActiveFilters}
					onClear={handleClearAllFilters}
				>
					{#snippet children(close)}
						<div class="col-span-2">
							<span class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1">Search mode</span>
							<div class="flex items-center gap-0.5 bg-surface-3/50 rounded p-0.5" role="radiogroup" aria-label="Search mode">
								{#each searchModes as searchMode}
									<button
										role="radio"
										aria-checked={currentState.filters.searchMode === searchMode.value}
										use:tooltip={searchMode.title}
										class="flex-1 px-2 py-1 text-xs rounded-sm transition-colors duration-100 {currentState.filters.searchMode === searchMode.value
											? 'bg-signal/10 text-signal'
											: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => handleSearchModeChange(searchMode.value)}
									>
										{searchMode.label}
									</button>
								{/each}
							</div>
						</div>

						<div class="col-span-2">
							<span class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1">Date</span>
							<div class="flex items-center gap-0.5 bg-surface-3/50 rounded p-0.5">
								{#each datePresets as preset}
									<button
										class="flex-1 px-2 py-1 text-xs rounded-sm transition-colors duration-100 {currentState.filters.datePreset === preset.value
											? 'bg-signal/10 text-signal'
											: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
										on:click={() => handleDatePresetChange(preset.value)}
									>
										{preset.label}
									</button>
								{/each}
							</div>
						</div>

						<div>
							<label class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1" for="history-filter-status">
								Status
							</label>
							<select
								id="history-filter-status"
								class="input text-xs py-1.5 px-2 bg-surface-3/50 w-full"
								value={currentState.filters.status}
								on:change={(e) => handleStatusChange(e.currentTarget.value)}
							>
								<option value="all">All Status</option>
								<option value="completed">Completed</option>
								<option value="failed">Failed</option>
								<option value="cancelled">Cancelled</option>
								<option value="pending">Pending</option>
								<option value="running">Running</option>
							</select>
						</div>

						{#if facets.modes.length > 0}
							<div>
								<label class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1" for="history-filter-mode">
									Mode
								</label>
								<select
									id="history-filter-mode"
									class="input text-xs py-1.5 px-2 bg-surface-3/50 w-full"
									value={currentState.filters.mode ?? ''}
									on:change={(e) => handleModeChange(e.currentTarget.value)}
								>
									<option value="">All Modes</option>
									{#each facets.modes as m}
										<option value={m.value}>{modeLabel(m.value)} ({m.count})</option>
									{/each}
								</select>
							</div>
						{/if}

						<div class={facets.modes.length > 0 ? '' : 'col-span-2'}>
							<span class="block text-xs uppercase tracking-[0.07em] text-fg-subtle mb-1">Model</span>
							<ModelPickTrigger
								id="history-filter-model"
								class="text-xs py-1.5 px-2 bg-surface-3/50"
								value={currentState.filters.modelName || null}
								placeholder="All Models"
								onopen={() => {
								close();
								openModelPicker();
							}}
								onclear={() => handleModelChange('')}
							/>
						</div>

						<div>
							<label class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1" for="history-filter-rating">
								Min rating
							</label>
							<select
								id="history-filter-rating"
								class="input text-xs py-1.5 px-2 bg-surface-3/50 w-full"
								value={currentState.filters.minRating ?? 0}
								on:change={(e) => handleMinRatingChange(parseInt(e.currentTarget.value))}
							>
								{#each minRatingOptions as opt}
									<option value={opt.value}>{opt.label}</option>
								{/each}
							</select>
						</div>

						<div class="col-span-2">
							<span class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1">Phrasebook used</span>
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
								class="text-xs text-fg-muted hover:text-fg transition-colors flex items-center gap-1"
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
				</FiltersPopover>
			</div>

			<!-- Right: view controls + actions -->
			<div class="flex items-center gap-2 ml-auto flex-shrink-0">
				<!-- Sort -->
				<select
					class="hidden md:block input text-xs py-1.5 px-2 bg-surface-2/50 w-auto"
					value={currentSortValue}
					on:change={(e) => handleSortChange(e.currentTarget.value)}
				>
					{#each sortOptions as opt}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>

				<!-- Tile size -->
				<div
					class="hidden md:flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5"
					role="radiogroup"
					aria-label="Tile size"
				>
					{#each tileSizes as size}
						<button
							role="radio"
							aria-checked={$historyTileSize === size.value}
							use:tooltip={size.title}
							class="w-7 py-1 font-mono text-sm rounded-sm transition-colors duration-100 {$historyTileSize === size.value
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
							on:click={() => historyTileSize.set(size.value)}
						>
							{size.label}
						</button>
					{/each}
				</div>

				<HistorySidebar part="actions" />
				<IconButton icon="refresh" label="Refresh" onclick={() => historyStore.loadGenerations()} />

				<!-- Overflow menu: rarer actions -->
				<div class="relative history-more-menu" bind:this={moreMenuButtonEl}>
					<Tooltip text="More actions">
						<IconButton
							icon="more"
							label="More actions"
							active={isMoreMenuOpen}
							onclick={toggleMoreMenu}
						/>
					</Tooltip>
					{#if isMoreMenuOpen}
						<div
							use:portal
							use:overlayLayer
							bind:this={moreMenuPanelEl}
							use:dismiss={{ onDismiss: dismissFrom('.history-more-menu', closeMoreMenu) }}
							class="fixed z-overlay min-w-[180px] bg-surface-2 rounded-xl border border-line-strong shadow-floating py-1"
							style="top: {moreMenuPosition.top}px; left: {moreMenuPosition.left}px;"
							role="menu"
							aria-label="More actions"
						>
							<button
								class="w-full px-3 py-2 text-left text-xs hover:bg-surface-3/50 transition-colors flex items-center gap-2 text-fg-muted hover:text-fg"
								role="menuitem"
								on:click={() => {
									closeMoreMenu();
									onOpenUpload();
								}}
							>
								<Icon name="upload" className="w-3.5 h-3.5" />
								Upload generations
							</button>
							<button
								class="w-full px-3 py-2 text-left text-xs hover:bg-surface-3/50 transition-colors flex items-center gap-2 text-fg-muted hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed"
								role="menuitem"
								disabled={importing}
								on:click={() => {
									closeMoreMenu();
									triggerImportBundle();
								}}
							>
								<Icon name="box" className="w-3.5 h-3.5" />
								{importing ? 'Importing…' : 'Import bundle'}
							</button>
							<button
								class="w-full px-3 py-2 text-left text-xs hover:bg-surface-3/50 transition-colors flex items-center gap-2 text-fg-muted hover:text-fg"
								role="menuitem"
								on:click={() => {
									closeMoreMenu();
									onOpenAddTag();
								}}
							>
								<Icon name="plus" className="w-3.5 h-3.5" />
								Add tag
							</button>
							<div class="my-1 h-px bg-line"></div>
							<button
								class="w-full px-3 py-2 text-left text-xs hover:bg-danger/10 transition-colors flex items-center gap-2 text-danger"
								role="menuitem"
								on:click={() => {
									closeMoreMenu();
									onOpenDeleteByCriteria();
								}}
							>
								<Icon name="trash" className="w-3.5 h-3.5" />
								Delete by criteria
							</button>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Active-filter summary: only for filters tucked into the popover, so hidden state is never invisible -->
		{#if advancedActiveCount > 0}
			<div class="hidden md:flex items-center gap-1.5 flex-wrap">
				<Icon name="filter" className="w-3 h-3 text-fg-subtle flex-shrink-0" />

				{#if currentState.filters.searchMode === 'semantic'}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Back to keyword search'}
						on:click={() => handleSearchModeChange('keyword')}
					>
						<span>Semantic search</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.datePreset !== 'all'}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Clear date filter'}
						on:click={() => handleDatePresetChange('all')}
					>
						<span class="truncate max-w-[8rem]">
							{datePresets.find((p) => p.value === currentState.filters.datePreset)?.label}
						</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.status !== 'all'}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0 capitalize"
						use:tooltip={'Clear status filter'}
						on:click={() => handleStatusChange('all')}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.status}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.mode}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Clear mode filter'}
						on:click={() => handleModeChange('')}
					>
						<span class="truncate max-w-[8rem]">{modeLabel(currentState.filters.mode)}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.modelName}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						aria-label="Clear model filter"
						on:click={() => handleModelChange('')}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.modelName}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.minRating}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Clear rating filter'}
						on:click={() => handleMinRatingChange(0)}
					>
						<span>★{currentState.filters.minRating}+</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.usedPhrasebookValueId}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Clear phrasebook filter'}
						on:click={() => handleUsedPhrasebookSelect(null, null)}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.usedPhrasebookLabel}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				<!-- Active auto-tag facet (set by clicking a system tag on an item) -->
				{#if currentState.filters.systemTag}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Clear auto-tag filter'}
						on:click={handleSystemTagClear}
					>
						<Icon name="sparkles" className="w-3 h-3" />
						<span class="truncate max-w-[8rem]">{currentState.filters.systemTag.replace(/_/g, ' ')}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if excludingSubcollections}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						use:tooltip={'Include subcollections again'}
						on:click={() => handleDirectOnlyChange(false)}
					>
						<span>Without subcollections</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if canMakeRule}
					<button
						class="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors ml-1 flex-shrink-0"
						on:click={handleMakeRule}
					>
						<Icon name="sparkles" className="w-3 h-3" />
						Make a rule from this filter
					</button>
				{/if}
				<button
					class="text-xs text-fg-muted hover:text-fg transition-colors ml-1 flex-shrink-0"
					on:click={handleClearAllFilters}
				>
					Clear all
				</button>
			</div>
		{/if}
	</div>
</PageHeader>
