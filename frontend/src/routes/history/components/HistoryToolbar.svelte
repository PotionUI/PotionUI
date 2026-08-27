<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import portal from '$lib/actions/portal';
	import { historyStore } from '$lib/stores/history';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { logger } from '$lib/utils/logger';
	import { buildImportBundleTabData } from '$lib/utils/historyReuse';
	import { PageHeader, IconButton, Badge } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import HistorySearchableFilter from './HistorySearchableFilter.svelte';
	import type { HistorySearchMode, SortBy, SortDir } from '$lib/types/history';
	import { computeAnchoredMenuPosition } from '$lib/utils/menuPosition';

	// Self-contained: reads/writes historyStore directly. Modal-opening
	// callbacks stay as props since modal state lives on the page.
	export let onOpenUpload: () => void;
	export let onOpenAddTag: () => void;
	export let onOpenDeleteByTags: () => void;

	$: currentState = $historyStore;
	$: facets = currentState.facets;

	let isMoreMenuOpen = false;
	let isFiltersOpen = false;
	let filtersButtonEl: HTMLButtonElement;
	let filtersPanelEl: HTMLDivElement;
	let filtersPanelPosition = { top: 0, left: 0 };
	let moreMenuButtonEl: HTMLDivElement;
	let moreMenuPanelEl: HTMLDivElement;
	let moreMenuPosition = { top: 0, left: 0 };

	// The toolbar's sticky header (+page.svelte) establishes its own stacking
	// context (position:sticky + z-index), which caps any z-index inside it —
	// the grid's per-card selection checkbox (GenerationCard.svelte, z-40) sits
	// outside that context entirely and paints above it regardless of the
	// panel's own z-index. `portal` (see $lib/actions/portal.ts) renders the
	// panel at body level to escape that ancestor stacking context instead of
	// fighting it with a bigger number. Both dropdowns below use it.

	function updateFiltersPanelPosition() {
		if (!filtersButtonEl) return;
		const panelWidth = filtersPanelEl?.getBoundingClientRect().width ?? 352;
		filtersPanelPosition = computeAnchoredMenuPosition(filtersButtonEl, { width: panelWidth });
	}

	function toggleFiltersPanel() {
		isFiltersOpen = !isFiltersOpen;
		if (isFiltersOpen) {
			updateFiltersPanelPosition();
			// The button's position is known immediately; the panel's own width
			// isn't until it exists in the DOM, so refine once it has mounted.
			requestAnimationFrame(updateFiltersPanelPosition);
		}
	}

	function closeFiltersPanel() {
		isFiltersOpen = false;
	}

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
		{ value: 'file_size:desc', label: 'Largest' }
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

	async function handleMediaTypeChange(mediaType: 'all' | 'image' | 'video') {
		historyStore.setFilter('mediaType', mediaType);
		await historyStore.loadGenerations();
	}

	// Search drives server-side full-text or semantic visual search, debounced.
	let searchDebounce: ReturnType<typeof setTimeout> | undefined;
	function handleSearchInput(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		historyStore.setFilter('search', value);
		clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => historyStore.loadGenerations(), 300);
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

	onDestroy(() => clearTimeout(searchDebounce));

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

	let importInput: HTMLInputElement;
	let importing = false;

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
				toasts.error(`Preset "${reuse.preset_id}" is not installed — install it before reusing this bundle.`);
				return;
			}

			const tabName = `Imported: ${reuse.preset_id.split('/').pop()}`;
			const { tabData } = buildImportBundleTabData(reuse);
			tabsStore.addTabWithData(tabName, tabData);
			goto('/generate');
		} catch (e) {
			logger.error('Import bundle failed:', e);
			toasts.error('Import failed. Please try again.');
		} finally {
			importing = false;
		}
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		// Both panels live at body level (portalled) — only their trigger stays
		// inside `.history-more-menu`/`.history-filters-menu`, so dismissal also
		// has to check containment against the portalled node directly.
		if (
			isMoreMenuOpen &&
			!target.closest('.history-more-menu') &&
			!(moreMenuPanelEl && moreMenuPanelEl.contains(target))
		) {
			closeMoreMenu();
		}
		if (
			isFiltersOpen &&
			!target.closest('.history-filters-menu') &&
			!(filtersPanelEl && filtersPanelEl.contains(target))
		) {
			closeFiltersPanel();
		}
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		if (isMoreMenuOpen) closeMoreMenu();
		if (isFiltersOpen) closeFiltersPanel();
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
		!!currentState.filters.usedPhrasebookValueId;

	// Filters tucked into the "Filters" popover — surfaced via the trigger's
	// badge count and the active-filter chip row so their state is never hidden.
	$: advancedActiveCount =
		(currentState.filters.datePreset !== 'all' ? 1 : 0) +
		(currentState.filters.status !== 'all' ? 1 : 0) +
		(currentState.filters.mode ? 1 : 0) +
		(currentState.filters.modelName ? 1 : 0) +
		(currentState.filters.minRating ? 1 : 0) +
		(currentState.filters.usedPhrasebookValueId ? 1 : 0) +
		(currentState.filters.systemTag ? 1 : 0);
</script>

<svelte:window on:click={handleWindowClick} on:keydown={handleWindowKeydown} />

<input
	type="file"
	accept=".zip,.json"
	class="hidden"
	bind:this={importInput}
	on:change={handleImportBundleChange}
/>

<PageHeader wrap sticky={false}>
	<div class="flex flex-col gap-2 w-full">
		<!-- Primary row: never wraps from ~1280px up. Below md it wraps onto two
			lines — title+actions, then the search bar spans the full width on its
			own line — via `order-last` on the search block rather than reordering
			the DOM, so the flex-wrap line break lands after title+actions instead
			of splitting them across lines. -->
		<div class="flex flex-wrap md:flex-nowrap items-center gap-2 md:gap-4 w-full">
			<!-- Left: Title + count -->
			<div class="flex items-baseline gap-3 flex-shrink-0">
				<span class="text-sm font-semibold text-fg">History</span>
				<span class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-subtle whitespace-nowrap">
					{currentState.totalCount} generations
				</span>
			</div>

			<!-- Divider -->
			<div class="hidden md:block h-6 w-px bg-line-strong flex-shrink-0"></div>

			<!-- Search - the anchor control. Full width of its own wrapped line on
				mobile (the search-mode toggle's min-content width doesn't fit
				alongside it in the flex-1/min-w-[12rem] box below md, which used to
				overflow past the viewport and get silently clipped). -->
			<div class="order-last md:order-none flex items-center gap-1.5 w-full md:w-auto md:flex-1 md:min-w-[12rem] md:max-w-md">
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
				<div
					class="hidden md:flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5"
					role="radiogroup"
					aria-label="Search mode"
				>
					{#each searchModes as searchMode}
						<button
							role="radio"
							aria-checked={currentState.filters.searchMode === searchMode.value}
							title={searchMode.title}
							class="px-2 py-1 text-xs rounded-sm transition-colors duration-100 {currentState.filters.searchMode === searchMode.value
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
							on:click={() => handleSearchModeChange(searchMode.value)}
						>
							{searchMode.label}
						</button>
					{/each}
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
						title="Images only"
						aria-label="Images only"
						on:click={() => handleMediaTypeChange('image')}
					>
						<Icon name="image" className="w-3.5 h-3.5" />
					</button>
					<button
						class="p-1.5 rounded-sm transition-colors duration-100 {currentState.filters.mediaType === 'video'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						title="Videos only"
						aria-label="Videos only"
						on:click={() => handleMediaTypeChange('video')}
					>
						<Icon name="video" className="w-3.5 h-3.5" />
					</button>
				</div>

				<!-- Favorites -->
				<button
					class="p-1.5 rounded transition-colors duration-100 bg-surface-2/50 {currentState.filters
						.favoritesOnly
						? 'text-signal'
						: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
					title="Favorites only"
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

				<!-- Filters popover: date, status, mode, model, rating, phrasebook-used -->
				<div class="relative history-filters-menu">
					<button
						bind:this={filtersButtonEl}
						type="button"
						class="input text-xs py-1.5 px-2.5 bg-surface-2/50 w-auto flex items-center gap-1.5 {advancedActiveCount > 0
							? 'text-signal'
							: 'text-fg-muted'}"
						aria-haspopup="dialog"
						aria-expanded={isFiltersOpen}
						on:click={toggleFiltersPanel}
					>
						<Icon name="filter" className="w-3.5 h-3.5" />
						Filters
						{#if advancedActiveCount > 0}
							<Badge variant="signal" size="sm">{advancedActiveCount}</Badge>
						{/if}
					</button>
					{#if isFiltersOpen}
						<div
							use:portal
							bind:this={filtersPanelEl}
							class="fixed z-[9999] w-[22rem] max-w-[90vw] bg-surface-2 rounded-xl border border-line-strong shadow-floating p-3"
							style="top: {filtersPanelPosition.top}px; left: {filtersPanelPosition.left}px;"
							role="dialog"
							aria-label="Advanced filters"
						>
							<div class="grid grid-cols-2 gap-2.5">
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
												<option value={m.value}>{m.value} ({m.count})</option>
											{/each}
										</select>
									</div>
								{/if}

								{#if facets.models.length > 0}
									<div class={facets.modes.length > 0 ? '' : 'col-span-2'}>
										<label class="block text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1" for="history-filter-model">
											Model
										</label>
										<select
											id="history-filter-model"
											class="input text-xs py-1.5 px-2 bg-surface-3/50 w-full"
											value={currentState.filters.modelName ?? ''}
											on:change={(e) => handleModelChange(e.currentTarget.value)}
										>
											<option value="">All Models</option>
											{#each facets.models as m}
												<option value={m.name}>{m.name} ({m.count})</option>
											{/each}
										</select>
									</div>
								{/if}

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
							</div>

							<div class="mt-3 pt-2.5 border-t border-line-strong/70 flex items-center justify-between">
								<span class="text-2xs text-fg-subtle">{advancedActiveCount} active</span>
								<button
									class="text-xs text-fg-muted hover:text-fg transition-colors flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
									disabled={!hasActiveFilters}
									on:click={handleClearAllFilters}
								>
									<Icon name="close" className="w-3 h-3" />
									Clear all
								</button>
							</div>
						</div>
					{/if}
				</div>
			</div>

			<!-- Spacer -->
			<div class="flex-1 hidden md:block"></div>

			<!-- Right: view controls + actions -->
			<div class="flex items-center gap-2 ml-auto md:ml-0 flex-shrink-0">
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
							title={size.title}
							class="w-6 py-1 font-mono text-2xs rounded-sm transition-colors duration-100 {$historyTileSize === size.value
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
							on:click={() => historyTileSize.set(size.value)}
						>
							{size.label}
						</button>
					{/each}
				</div>

				<IconButton icon="refresh" label="Refresh" onclick={() => historyStore.loadGenerations()} />

				<!-- Overflow menu: rarer actions -->
				<div class="relative history-more-menu" bind:this={moreMenuButtonEl}>
					<IconButton
						icon="more"
						label="More actions"
						active={isMoreMenuOpen}
						onclick={toggleMoreMenu}
					/>
					{#if isMoreMenuOpen}
						<div
							use:portal
							bind:this={moreMenuPanelEl}
							class="fixed z-[9999] min-w-[180px] bg-surface-2 rounded-xl border border-line-strong shadow-floating py-1"
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
									onOpenDeleteByTags();
								}}
							>
								<Icon name="trash" className="w-3.5 h-3.5" />
								Delete by tags
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

				{#if currentState.filters.datePreset !== 'all'}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						title="Clear date filter"
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
						title="Clear status filter"
						on:click={() => handleStatusChange('all')}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.status}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.mode}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						title="Clear mode filter"
						on:click={() => handleModeChange('')}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.mode}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.modelName}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						title="Clear model filter"
						on:click={() => handleModelChange('')}
					>
						<span class="truncate max-w-[8rem]">{currentState.filters.modelName}</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.minRating}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						title="Clear rating filter"
						on:click={() => handleMinRatingChange(0)}
					>
						<span>★{currentState.filters.minRating}+</span>
						<Icon name="close" className="w-3 h-3" />
					</button>
				{/if}

				{#if currentState.filters.usedPhrasebookValueId}
					<button
						class="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-dashed border-line bg-surface-2/50 text-xs text-fg-muted hover:text-fg transition-colors flex-shrink-0"
						title="Clear phrasebook filter"
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
						title="Clear auto-tag filter"
						on:click={handleSystemTagClear}
					>
						<Icon name="sparkles" className="w-3 h-3" />
						<span class="truncate max-w-[8rem]">{currentState.filters.systemTag.replace(/_/g, ' ')}</span>
						<Icon name="close" className="w-3 h-3" />
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
