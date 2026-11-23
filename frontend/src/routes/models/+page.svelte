<script lang="ts">
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { collectionQueryParams } from '$lib/components/collections/selection';
	import { goto } from '$app/navigation';
	import { replaceUrl } from '$lib/utils/replaceUrl';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { isAxiosError } from 'axios';
	import { api } from '$lib/services/api/index';
	import { tagCatalog } from '$lib/stores/tagCatalog';
	import { debounce } from '$lib/utils/time';
	import { createLatestRequest } from '$lib/models/latestRequest';
		import { authStore } from '$lib/stores/auth';
	import { resolveInstallModelsTarget } from '$lib/utils/installModelsTarget';
	import { recipeCatalog, loadRecipeCatalog } from '$lib/stores/recipeCatalog';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import QuickTagFilterBar from '$lib/components/QuickTagFilterBar.svelte';
	import { Pagination, EmptyState, Button, Menu } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderToggle,
		HeaderSelect,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		HeaderDropdown,
		type HeaderStage,
		type FilterChip
	} from '$lib/components/page-header';
	import ModelLibrarySidebar from './components/ModelLibrarySidebar.svelte';
	import CollectionSidebarSheet from '$lib/components/collections/CollectionSidebarSheet.svelte';
	import IncludeSubcollections from '$lib/components/collections/IncludeSubcollections.svelte';
	import { excludesSubcollections } from '$lib/components/collections/subcollections';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import ModelSelectionToolbar from './components/ModelSelectionToolbar.svelte';

	// This is the user-facing library. It shows nothing operational - no file size,
	// no indexing timestamp, no backend availability - regardless of who is looking.
	// Admins get that view in Admin -> Models. See docs/models.md.

	let models: any[] = [];
	let modelTypes: any[] = [];
	let availableTags: any[] = [];
	let loading = true;
	let selectedType = 'all';
	let selectedTags: string[] = [];
	let searchQuery = '';
	let appliedSearch = '';
	let searchMode: 'substring' | 'regex' = 'substring';
	let searchError: string | null = null;
	let typesRequest: AbortController | undefined;
	let sortBy = 'filename';
	let sortOrder = 'desc';
	let currentPage = 1;
	let itemsPerPage = 48;
	let totalCount = 0;
	let initialized = false;
	let sidebarOpen = true;
	let favoritesOnly = false;
	let collectionId: string | undefined = undefined;
	let unsorted = false;
	let directOnly = false;
	let indexedFrom = '';
	let indexedTo = '';
	let stage: HeaderStage = 'full';
	let filtersOpen = false;
	let unfilteredTotal: number | null = null;

	// Multi-select mode: mirrors History's page-local selectionMode/selectedGenerationIds,
	// but the models grid has no global store, so this stays local to the page.
	let selectionMode = false;
	let selectedModelIds: string[] = [];

	// Toggle a model's selection from its card checkbox; turns selection mode on
	// when the first item is selected and off when the last one is cleared.
	function toggleModelSelect(model: any) {
		const isSelected = selectedModelIds.includes(model.id);
		selectedModelIds = isSelected
			? selectedModelIds.filter((id) => id !== model.id)
			: [...selectedModelIds, model.id];
		selectionMode = selectedModelIds.length > 0;
	}

	function selectAllOnPage() {
		selectedModelIds = models.map((m) => m.id);
		selectionMode = selectedModelIds.length > 0;
	}

	function clearModelSelection() {
		selectedModelIds = [];
		selectionMode = false;
	}

	function exitSelectionMode() {
		selectedModelIds = [];
		selectionMode = false;
	}

	$: totalPages = Math.ceil(totalCount / itemsPerPage);
	$: hasActiveFilters =
		selectedType !== 'all' || selectedTags.length > 0 || searchQuery !== '' || !!indexedFrom || !!indexedTo;
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	// An admin looking at an empty model library can install the models a
	// recipe brings; without a recipe catalog (or as a non-admin) the page
	// keeps its /setup link.
	$: if (isAdmin && !hasActiveFilters && models.length === 0) void loadRecipeCatalog();
	$: installModels = resolveInstallModelsTarget($recipeCatalog, null, isAdmin);

	// Update URL when filters change (after initialization)
	$: if (browser && initialized && (selectedType || appliedSearch !== undefined || searchMode || selectedTags || sortBy || sortOrder || indexedFrom || indexedTo || currentPage || itemsPerPage || favoritesOnly || collectionId || unsorted || directOnly)) {
		updateUrlParams();
	}

	function updateUrlParams() {
		const params = new URLSearchParams();
		if (selectedType !== 'all') params.set('type', selectedType);
		if (appliedSearch) params.set('q', appliedSearch);
		if (searchMode === 'regex') params.set('q_mode', 'regex');
		if (selectedTags.length > 0) params.set('tags', selectedTags.join(','));
		if (sortBy !== 'filename') params.set('sort', sortBy);
		if (sortOrder !== 'desc') params.set('order', sortOrder);
		if (currentPage > 1) params.set('page', currentPage.toString());
		if (itemsPerPage !== 48) params.set('per_page', itemsPerPage.toString());
		if (favoritesOnly) params.set('favorites', 'true');
		if (collectionId) params.set('collection', collectionId);
		if (unsorted) params.set('unsorted', 'true');
		if (directOnly) params.set('direct', 'true');
		if (indexedFrom) params.set('from', indexedFrom);
		if (indexedTo) params.set('to', indexedTo);

		const newUrl = params.toString() ? `?${params.toString()}` : '/models';
		replaceUrl(newUrl);
	}

	function loadFiltersFromUrl() {
		const params = $page.url.searchParams;
		selectedType = params.get('type') || 'all';
		searchQuery = params.get('q') || '';
		searchMode = params.get('q_mode') === 'regex' ? 'regex' : 'substring';
		const tagsParam = params.get('tags');
		selectedTags = tagsParam ? tagsParam.split(',') : [];
		sortBy = params.get('sort') || 'filename';
		sortOrder = params.get('order') || 'desc';
		currentPage = parseInt(params.get('page') || '1', 10);
		itemsPerPage = parseInt(params.get('per_page') || '48', 10);
		favoritesOnly = params.get('favorites') === 'true';
		collectionId = params.get('collection') || undefined;
		unsorted = params.get('unsorted') === 'true';
		directOnly = params.get('direct') === 'true';
		indexedFrom = params.get('from') || '';
		indexedTo = params.get('to') || '';
	}

	function selectAllModels() {
		favoritesOnly = false;
		unsorted = false;
		collectionId = undefined;
		currentPage = 1;
	}

	function selectFavorites() {
		favoritesOnly = true;
		unsorted = false;
		collectionId = undefined;
		currentPage = 1;
	}

	function selectUnsorted() {
		favoritesOnly = false;
		unsorted = true;
		collectionId = undefined;
		currentPage = 1;
	}

	function setDirectOnly(value: boolean) {
		directOnly = value;
		currentPage = 1;
	}

	function selectCollection(id: string) {
		// An empty id means "the active collection was deleted" - fall back to all.
		collectionId = id || undefined;
		favoritesOnly = false;
		unsorted = false;
		currentPage = 1;
	}

	const modelsRequest = createLatestRequest();
	const applySearch = debounce((value: string) => {
		appliedSearch = value;
	}, 300);

	$: if (initialized) applySearch(searchQuery);

	onMount(async () => {
		loadFiltersFromUrl();
		appliedSearch = searchQuery;
		await Promise.all([loadModelTypes(), loadTags()]);
		await loadModels();
		loading = false;
		initialized = true;
	});

	onDestroy(() => {
		applySearch.cancel();
		modelsRequest.invalidate();
		typesRequest?.abort();
	});

	function refetch() {
		if (!initialized) return;
		void loadModels();
		void loadModelTypes();
	}


	$: {
		currentPage;
		selectedType;
		appliedSearch;
		searchMode;
		selectedTags;
		sortBy;
		sortOrder;
		itemsPerPage;
		favoritesOnly;
		collectionId;
		unsorted;
		directOnly;
		indexedFrom;
		indexedTo;
		refetch();
	}

	async function loadModels() {
		const ticket = modelsRequest.begin();
		try {
			const response = await api.getModels({
				model_type: selectedType !== 'all' ? selectedType : undefined,
				search: appliedSearch || undefined,
				q_mode: appliedSearch && searchMode === 'regex' ? 'regex' : undefined,
				sort_by: sortBy,
				sort_order: sortOrder,
				limit: itemsPerPage,
				offset: (currentPage - 1) * itemsPerPage,
				include_tags: true,
				tag_ids: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
				favorites_only: favoritesOnly || undefined,
				indexed_from: indexedFrom || undefined,
				indexed_to: indexedTo || undefined,
				...collectionQueryParams({ collectionId, unsorted, directOnly })
			});

			if (!modelsRequest.isCurrent(ticket)) return;
			if (response.success && response.data) {
				models = response.data.models;
				totalCount = response.data.total;
				searchError = null;
			}
		} catch (error) {
			if (!modelsRequest.isCurrent(ticket)) return;
			if (isAxiosError(error) && error.response?.status === 422) {
				searchError = getApiErrorMessage(error, 'Invalid search');
				models = [];
				totalCount = 0;
				return;
			}
			logger.error('Failed to load models:', error);
		}
	}

	async function loadModelTypes() {
		typesRequest?.abort();
		const request = new AbortController();
		typesRequest = request;
		try {
			const response = await api.getModelTypes(
				{
					user_scoped: true,
					search: appliedSearch || undefined,
					q_mode: appliedSearch && searchMode === 'regex' ? 'regex' : undefined,
					tag_ids: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
					favorites_only: favoritesOnly || undefined,
					indexed_from: indexedFrom || undefined,
					indexed_to: indexedTo || undefined,
					...collectionQueryParams({ collectionId, unsorted, directOnly })
				},
				request.signal
			);
			if (request.signal.aborted) return;
			if (response.success && response.data) {
				modelTypes = response.data.types;
			}
		} catch (error) {
			if (request.signal.aborted || (isAxiosError(error) && error.response?.status === 422)) return;
			logger.error('Failed to load model types:', error);
		}
	}

	async function loadTags() {
		try {
			availableTags = await tagCatalog.get('MODEL', { fresh: true });
		} catch (error) {
			logger.error('Failed to load tags:', error);
		}
	}

	function handleModelView(event: CustomEvent) {
		const model = event.detail;
		// Store current URL for back navigation
		if (browser) {
			sessionStorage.setItem('modelsListUrl', window.location.href);
		}
		goto(`/models/${model.id}`);
	}

	function handlePageChange(newPage: number) {
		currentPage = newPage;
	}

	function handleTypeChange(type: string) {
		selectedType = type;
		currentPage = 1;
	}

	function handleTagToggle(tagId: string) {
		if (selectedTags.includes(tagId)) {
			selectedTags = selectedTags.filter((id) => id !== tagId);
		} else {
			selectedTags = [...selectedTags, tagId];
		}
		currentPage = 1;
	}

	function handleClearAllFilters() {
		searchQuery = '';
		searchMode = 'substring';
		selectedType = 'all';
		selectedTags = [];
		indexedFrom = '';
		indexedTo = '';
		currentPage = 1;
	}

	const perPageOptions = [12, 24, 48, 96].map((value) => ({ value: String(value), label: String(value) }));

	const labelClass = 'block text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1';

	const sortOptions = [
		{ value: 'filename', label: 'Name' },
		{ value: 'model_type', label: 'Type' },
		{ value: 'indexed_at', label: 'Date added' },
		{ value: 'modified_at', label: 'Modified' },
		{ value: 'file_size', label: 'Size' }
	];

	function handleSortChange(value: string) {
		sortBy = value;
		currentPage = 1;
	}

	function handleSortDirChange(dir: 'asc' | 'desc') {
		sortOrder = dir;
		currentPage = 1;
	}

	const capitalize = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

	$: typeOptions = [
		{ value: 'all', label: 'All', pinned: true },
		...modelTypes.map((type) => ({ value: type.type, label: capitalize(type.type), count: type.count }))
	];

	$: excludingSubcollections = excludesSubcollections($modelLibraryStore.collections, collectionId, directOnly);

	function buildChips(type: string, from: string, to: string, excluding: boolean): FilterChip[] {
		const list: FilterChip[] = [];
		if (type !== 'all') list.push({ id: 'type', label: capitalize(type), onremove: () => handleTypeChange('all') });
		if (from || to) {
			list.push({
				id: 'added',
				label: `Added ${from || '…'} – ${to || '…'}`,
				onremove: () => {
					indexedFrom = '';
					indexedTo = '';
					currentPage = 1;
				}
			});
		}
		if (excluding) list.push({ id: 'subcollections', label: 'Without subcollections', onremove: () => setDirectOnly(false) });
		return list;
	}

	$: chips = buildChips(selectedType, indexedFrom, indexedTo, excludingSubcollections);

	$: if (!hasActiveFilters && !collectionId && !favoritesOnly && !unsorted && !loading) unfilteredTotal = totalCount;

	function toggleSearchMode() {
		searchMode = searchMode === 'regex' ? 'substring' : 'regex';
		currentPage = 1;
	}

	function handleItemsPerPageChange(perPage: number) {
		itemsPerPage = perPage;
		currentPage = 1;
	}
</script>

{#snippet modelCollections(part: 'sidebar' | 'actions', onCollapse?: () => void)}
	<ModelLibrarySidebar
		{part}
		activeCollectionId={collectionId}
		{favoritesOnly}
		{unsorted}
		{directOnly}
		onSelectUnsorted={selectUnsorted}
		onSelectAll={selectAllModels}
		onSelectFavorites={selectFavorites}
		onSelectCollection={selectCollection}
		{onCollapse}
	/>
{/snippet}

<div class="flex h-[calc(100dvh-var(--tab-bar-height))] md:h-[100dvh] bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the grid scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-[272px] flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				{@render modelCollections('sidebar', () => (sidebarOpen = false))}
			</div>
		</aside>
	{/if}

	<!-- Right column: existing grid content -->
	<div class="flex-1 min-w-0 min-h-0 overflow-y-auto flex flex-col dot-grid bg-canvas">
	<!-- Top Bar with Filters -->
	<div class="sticky top-0 z-30">
	<ListPageHeader bind:stage>
		{#snippet leading()}
			<CollectionSidebarSheet label="Models" collapsed={!sidebarOpen} onExpand={() => (sidebarOpen = true)}>
				{@render modelCollections('sidebar')}
			</CollectionSidebarSheet>
		{/snippet}

		{#snippet title(s)}
			<HeaderTitle
				title="Models"
				count={totalCount}
				unit="models"
				unitOne="model"
				total={unfilteredTotal}
				filterCount={chips.length}
				onOpenFilters={() => (filtersOpen = true)}
				showUnit={s === 'full'}
				narrow={s === 'narrow'}
			/>
		{/snippet}

		{#snippet search(s)}
			<HeaderSearch
				value={searchQuery}
				placeholder={searchMode === 'regex' ? 'Regular expression…' : 'Search models · * and ? are wildcards'}
				label="Search models"
				error={searchError}
				oninput={(value) => (searchQuery = value)}
				narrow={s === 'narrow'}
			>
				{#snippet modifiers()}
					<HeaderToggle
						icon="regex"
						label="Regular expression"
						pressed={searchMode === 'regex'}
						onclick={toggleSearchMode}
					/>
				{/snippet}
			</HeaderSearch>
		{/snippet}

		{#snippet facets(s)}
			{#if s !== 'tight' && s !== 'narrow'}
				<HeaderSelect
					label="Type"
					active={selectedType !== 'all'}
					value={selectedType}
					options={typeOptions}
					onchange={handleTypeChange}
				/>
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
				{#snippet children()}
					{#if s === 'narrow'}
						<div class="col-span-2">
							<span class={labelClass}>Sort</span>
							<HeaderSort
								options={sortOptions}
								value={sortBy}
								dir={sortOrder === 'asc' ? 'asc' : 'desc'}
								onchange={handleSortChange}
								ondir={handleSortDirChange}
							/>
						</div>
					{/if}
					{#if s === 'tight' || s === 'narrow'}
						<div class="col-span-2">
							<span class={labelClass}>Type</span>
							<HeaderSelect
								label="Type"
								active={selectedType !== 'all'}
								value={selectedType}
								options={typeOptions}
								onchange={handleTypeChange}
							/>
						</div>
					{/if}
					<div>
						<label class={labelClass} for="models-filter-from">Added from</label>
						<input
							id="models-filter-from"
							type="date"
							class="input text-sm py-1.5 px-2 bg-surface-3/50 w-full"
							bind:value={indexedFrom}
							on:change={() => (currentPage = 1)}
						/>
					</div>
					<div>
						<label class={labelClass} for="models-filter-to">Added to</label>
						<input
							id="models-filter-to"
							type="date"
							class="input text-sm py-1.5 px-2 bg-surface-3/50 w-full"
							bind:value={indexedTo}
							on:change={() => (currentPage = 1)}
						/>
					</div>
					<IncludeSubcollections
						collections={$modelLibraryStore.collections}
						activeId={collectionId}
						{directOnly}
						onDirectOnlyChange={setDirectOnly}
					/>
				{/snippet}
			</HeaderFilters>
		{/snippet}

		{#snippet sort()}
			<HeaderSort
				options={sortOptions}
				value={sortBy}
				dir={sortOrder === 'asc' ? 'asc' : 'desc'}
				onchange={handleSortChange}
				ondir={handleSortDirChange}
			/>
		{/snippet}

		{#snippet view()}
			{@render modelCollections('actions')}
			<HeaderViewOptions onrefresh={() => loadModels()} />
		{/snippet}

		{#snippet more(s)}
			{#if s === 'narrow'}
				<Menu
					label="More actions"
					tooltip="More actions"
					items={[{ id: 'refresh', label: 'Refresh', icon: 'refresh' }]}
					onselect={() => loadModels()}
					width={200}
				/>
			{/if}
		{/snippet}
	</ListPageHeader>

		<!-- Quick Tags Bar -->
		<QuickTagFilterBar
			tags={availableTags}
			selectedIds={selectedTags}
			onToggle={handleTagToggle}
			onClear={() => { selectedTags = []; currentPage = 1; }}
		>
			<svelte:fragment slot="overflow" let:overflowTags>
				{#if overflowTags.length > 0}
					<span class="flex-shrink-0 font-mono text-2xs text-fg-subtle">+{overflowTags.length}</span>
				{/if}
			</svelte:fragment>
		</QuickTagFilterBar>
	</div>

	<ModelSelectionToolbar
		active={selectionMode}
		selectedIds={selectedModelIds}
		totalOnPage={models.length}
		onSelectAll={selectAllOnPage}
		onClearSelection={clearModelSelection}
		onClose={exitSelectionMode}
		onFavoritesChanged={loadModels}
	/>

	<!-- Content Area -->
	<div class="{!loading && models.length === 0 ? '' : 'px-3 py-3 md:px-6 md:py-6'} flex-1 flex flex-col">

	<!-- Model Grid -->
	<div class="flex-1 flex flex-col">
		{#if loading}
			<div
				class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 gap-3 md:gap-6"
			>
				{#each Array(itemsPerPage) as _}
					<div class="group">
						<div class="relative rounded-lg overflow-hidden bg-surface-1 border border-line-strong">
							<div class="aspect-[3/4] w-full bg-surface-2 animate-pulse"></div>
						</div>
					</div>
				{/each}
			</div>
		{:else if models.length > 0}
			<div
				class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 gap-3 md:gap-6"
			>
				{#each models as model (model.id)}
					<ModelCard
						{model}
						on:view={handleModelView}
						selectable={selectionMode}
						showCheckbox={true}
						selected={selectedModelIds.includes(model.id)}
						onSelect={toggleModelSelect}
					/>
				{/each}
			</div>

			<!-- Pagination -->
			{#if totalPages > 1}
				<div class="mt-10 flex items-center justify-center gap-4 relative">
					<Pagination {currentPage} {totalPages} onPageChange={handlePageChange} />

					<div class="hidden md:flex items-center gap-2 absolute right-0">
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Per page</span>
						<HeaderDropdown
							ariaLabel="Per page"
							value={String(itemsPerPage)}
							options={perPageOptions}
							onchange={(value) => handleItemsPerPageChange(parseInt(value))}
						/>
					</div>
				</div>
			{/if}
		{:else}
			<EmptyState
				page
				icon={hasActiveFilters ? 'search' : 'model'}
				title={hasActiveFilters ? 'No Results Found' : 'No Models Available'}
				description={hasActiveFilters
					? "Try adjusting your search criteria or filters to find what you're looking for."
					: isAdmin
						? installModels
							? 'No models are available yet. Install the models a recipe brings, or add one directly in Downloads.'
							: 'No models are available yet. Run guided setup to install one, or add one directly in Downloads.'
						: 'No models are available yet. Ask your administrator to set up models for this instance.'}
			>
				{#snippet actions()}
					{#if hasActiveFilters}
						<Button variant="secondary" icon="close" onclick={handleClearAllFilters}>Clear All Filters</Button>
					{:else if isAdmin}
						{#if installModels}
							<Button variant="secondary" icon="download" href={installModels.href}>{installModels.label}</Button>
						{:else}
							<Button variant="secondary" icon="sparkles" href="/setup">Run guided setup</Button>
						{/if}
						<Button variant="secondary" icon="download" href="/admin?tab=downloads">Open Downloads</Button>
					{:else}
						<Button variant="secondary" href="/setup">Check setup status</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}
	</div>
	</div>
	</div>
</div>
