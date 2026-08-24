<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import { api } from '$lib/services/api/index';
	import { authStore } from '$lib/stores/auth';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import QuickTagFilterBar from '$lib/components/QuickTagFilterBar.svelte';
	import { PageHeader, IconButton, Pagination } from '$lib/components/ui';
	import ModelLibrarySidebar from './components/ModelLibrarySidebar.svelte';
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
	let sortBy = 'filename';
	let sortOrder = 'desc';
	let currentPage = 1;
	let itemsPerPage = 48;
	let totalCount = 0;
	let initialized = false;
	let sidebarOpen = true;
	let favoritesOnly = false;
	let collectionId: string | undefined = undefined;

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
		selectedType !== 'all' || selectedTags.length > 0 || searchQuery !== '';
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';

	// Update URL when filters change (after initialization)
	$: if (browser && initialized && (selectedType || searchQuery !== undefined || selectedTags || sortBy || sortOrder || currentPage || itemsPerPage || favoritesOnly || collectionId)) {
		updateUrlParams();
	}

	function updateUrlParams() {
		const params = new URLSearchParams();
		if (selectedType !== 'all') params.set('type', selectedType);
		if (searchQuery) params.set('q', searchQuery);
		if (selectedTags.length > 0) params.set('tags', selectedTags.join(','));
		if (sortBy !== 'filename') params.set('sort', sortBy);
		if (sortOrder !== 'desc') params.set('order', sortOrder);
		if (currentPage > 1) params.set('page', currentPage.toString());
		if (itemsPerPage !== 48) params.set('per_page', itemsPerPage.toString());
		if (favoritesOnly) params.set('favorites', 'true');
		if (collectionId) params.set('collection', collectionId);

		const newUrl = params.toString() ? `?${params.toString()}` : '/models';
		window.history.replaceState({}, '', newUrl);
	}

	function loadFiltersFromUrl() {
		const params = $page.url.searchParams;
		selectedType = params.get('type') || 'all';
		searchQuery = params.get('q') || '';
		const tagsParam = params.get('tags');
		selectedTags = tagsParam ? tagsParam.split(',') : [];
		sortBy = params.get('sort') || 'filename';
		sortOrder = params.get('order') || 'desc';
		currentPage = parseInt(params.get('page') || '1', 10);
		itemsPerPage = parseInt(params.get('per_page') || '48', 10);
		favoritesOnly = params.get('favorites') === 'true';
		collectionId = params.get('collection') || undefined;
	}

	function selectAllModels() {
		favoritesOnly = false;
		collectionId = undefined;
		currentPage = 1;
	}

	function selectFavorites() {
		favoritesOnly = true;
		collectionId = undefined;
		currentPage = 1;
	}

	function selectCollection(id: string) {
		// An empty id means "the active collection was deleted" - fall back to all.
		collectionId = id || undefined;
		favoritesOnly = false;
		currentPage = 1;
	}

	onMount(async () => {
		loadFiltersFromUrl();
		await Promise.all([loadModelTypes(), loadTags()]);
		await loadModels();
		loading = false;
		initialized = true;
	});


	$: {
		currentPage;
		selectedType;
		searchQuery;
		selectedTags;
		sortBy;
		sortOrder;
		itemsPerPage;
		favoritesOnly;
		collectionId;
		if (!loading) loadModels();
	}

	async function loadModels() {
		try {
			const response = await api.getModels({
				model_type: selectedType !== 'all' ? selectedType : undefined,
				search: searchQuery || undefined,
				sort_by: sortBy,
				sort_order: sortOrder,
				limit: itemsPerPage,
				offset: (currentPage - 1) * itemsPerPage,
				include_tags: true,
				tag_ids: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
				favorites_only: favoritesOnly || undefined,
				collection_id: collectionId
			});

			if (response.success && response.data) {
				models = response.data.models;
				totalCount = response.data.total;
			}
		} catch (error) {
			logger.error('Failed to load models:', error);
		}
	}

	async function loadModelTypes() {
		try {
			const response = await api.getModelTypes({ user_scoped: true });
			if (response.success && response.data) {
				modelTypes = response.data.types;
			}
		} catch (error) {
			logger.error('Failed to load model types:', error);
		}
	}

	async function loadTags() {
		try {
			const response = await api.getTags('MODEL');
			if (response.success && response.data) {
				availableTags = response.data.tags;
			}
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
		selectedType = 'all';
		selectedTags = [];
		currentPage = 1;
	}

	function handleItemsPerPageChange(perPage: number) {
		itemsPerPage = perPage;
		currentPage = 1;
	}
</script>

<div class="flex min-h-screen bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the grid scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-60 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<ModelLibrarySidebar
					activeCollectionId={collectionId}
					{favoritesOnly}
					onSelectAll={selectAllModels}
					onSelectFavorites={selectFavorites}
					onSelectCollection={selectCollection}
					onCollapse={() => (sidebarOpen = false)}
				/>
			</div>
		</aside>
	{:else}
		<aside class="hidden md:block w-8 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20">
			<button
				class="sticky top-0 flex h-screen w-full flex-col items-center gap-2 pt-3 text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
				on:click={() => (sidebarOpen = true)}
				title="Show library"
				aria-label="Show library"
			>
				<Icon name="chevron-right" className="w-4 h-4" />
				<Icon name="folder" className="w-4 h-4" />
			</button>
		</aside>
	{/if}

	<!-- Right column: existing grid content -->
	<div class="flex-1 min-w-0">
	<!-- Top Bar with Filters -->
	<div class="sticky top-0 z-30">
	<PageHeader wrap sticky={false}>
		<div class="flex items-center gap-2 md:gap-4 flex-wrap w-full">
			<!-- Left: Title + count -->
			<div class="flex items-baseline gap-3">
				<span class="text-sm font-semibold text-fg">Models</span>
				<span class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-subtle whitespace-nowrap">
					{totalCount} models
				</span>
			</div>

			<!-- Divider -->
			<div class="hidden md:block h-6 w-px bg-line-strong"></div>

			<!-- Search - the anchor control -->
			<div class="relative flex-1 min-w-[8rem] max-w-xs">
				<Icon
					name="search"
					className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
				/>
				<input
					type="text"
					class="input text-xs py-1.5 pl-8 pr-3 bg-surface-2/50 w-full"
					placeholder="Search models..."
					bind:value={searchQuery}
				/>
			</div>

			<!-- Filters cluster -->
			<div class="hidden md:flex items-center gap-2 flex-wrap">
				<!-- Type filter: wraps instead of scrolling — never shows a scrollbar -->
				<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5 flex-wrap">
					<button
						class="px-2 py-1 text-xs rounded-sm transition-colors duration-100 whitespace-nowrap {selectedType === 'all'
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						on:click={() => handleTypeChange('all')}
					>
						All
					</button>
					{#each modelTypes as type}
						<button
							class="px-2 py-1 text-xs rounded-sm transition-colors duration-100 whitespace-nowrap {selectedType === type.type
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
							on:click={() => handleTypeChange(type.type)}
						>
							{type.type.charAt(0).toUpperCase() + type.type.slice(1)}
							<span class="font-mono tabular-nums opacity-75">({type.count})</span>
						</button>
					{/each}
				</div>

				<!-- Sort By -->
				<select class="input w-auto pr-8 text-xs py-1.5 bg-surface-2/50" bind:value={sortBy}>
					<option value="filename">Name</option>
					<option value="model_type">Type</option>
				</select>

				<!-- Sort Order -->
				<select class="input w-auto pr-8 text-xs py-1.5 bg-surface-2/50" bind:value={sortOrder}>
					<option value="desc">Desc</option>
					<option value="asc">Asc</option>
				</select>

				{#if hasActiveFilters}
					<button
						class="text-xs text-fg-muted hover:text-fg transition-colors flex items-center gap-1 whitespace-nowrap"
						on:click={handleClearAllFilters}
					>
						<Icon name="close" className="w-3 h-3" />
						Clear
					</button>
				{/if}
			</div>

			<!-- Spacer -->
			<div class="flex-1 hidden md:block"></div>

			<!-- Right: Actions -->
			<div class="flex items-center gap-2 ml-auto md:ml-0">
				<IconButton icon="refresh" label="Refresh models" onclick={() => loadModels()} />
			</div>
		</div>
	</PageHeader>

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
	<div class="px-3 py-3 md:px-6 md:py-6">

	<!-- Model Grid -->
	<div>
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
						<select
							class="input text-xs py-1 px-2 bg-surface-2/50 w-auto font-mono tabular-nums"
							value={itemsPerPage}
							on:change={(e) => handleItemsPerPageChange(parseInt(e.currentTarget.value))}
						>
							<option value={12}>12</option>
							<option value={24}>24</option>
							<option value={48}>48</option>
							<option value={96}>96</option>
						</select>
					</div>
				</div>
			{/if}
		{:else}
			<div class="dot-grid text-center py-20 rounded-lg">
				<div class="max-w-md mx-auto">
					<div
						class="w-16 h-16 flex items-center justify-center mx-auto mb-6 rounded-lg bg-surface-2 border border-line"
					>
						<Icon
							name={hasActiveFilters ? 'search' : 'model'}
							className="h-8 w-8 text-fg-subtle"
							strokeWidth={1.5}
						/>
					</div>
					<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-3">
						{hasActiveFilters ? 'No Results Found' : 'No Models Available'}
					</p>
					<p class="text-fg-muted mb-8 leading-relaxed text-sm">
						{hasActiveFilters
							? "Try adjusting your search criteria or filters to find what you're looking for."
							: isAdmin
								? 'No models are available yet. Run guided setup to install one, or add one directly in Downloads.'
								: 'No models are available yet. Ask your administrator to set up models for this instance.'}
					</p>
					{#if hasActiveFilters}
						<button
							class="px-4 py-2 bg-surface-2 text-fg rounded hover:bg-surface-3 transition-colors font-medium inline-flex items-center gap-2 text-sm"
							on:click={handleClearAllFilters}
						>
							<Icon name="close" className="w-4 h-4" />
							Clear All Filters
						</button>
					{:else if isAdmin}
						<div class="flex items-center justify-center gap-3">
							<a
								href="/setup"
								class="px-4 py-2 bg-surface-2 text-fg rounded hover:bg-surface-3 transition-colors font-medium inline-flex items-center gap-2 text-sm"
							>
								<Icon name="sparkles" className="w-4 h-4" />
								Run guided setup
							</a>
							<a
								href="/admin?tab=downloads"
								class="px-4 py-2 bg-surface-2 text-fg rounded hover:bg-surface-3 transition-colors font-medium inline-flex items-center gap-2 text-sm"
							>
								<Icon name="download" className="w-4 h-4" />
								Open Downloads
							</a>
						</div>
					{:else}
						<a
							href="/setup"
							class="text-sm text-fg-subtle hover:text-fg-muted underline decoration-dotted"
						>
							Check setup status
						</a>
					{/if}
				</div>
			</div>
		{/if}
	</div>
	</div>
	</div>
</div>
