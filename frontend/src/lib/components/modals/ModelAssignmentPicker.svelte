<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { authStore } from '$lib/stores/auth';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { mediaFileThumbnailUrl } from '$lib/utils/modelPreview';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner, Pagination } from '$lib/components/ui';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let assignedModelIds: string[] = [];
	export let processingModelId: string | null = null;
	export let assignedUserId: string | undefined = undefined;
	export let assignedGroupId: string | undefined = undefined;
	export let onAssign: ((modelId: string) => void) | undefined = undefined;
	export let onUnassign: ((modelId: string) => void) | undefined = undefined;
	/** Reuse the admin model browser as a normal single-value picker, or as a
	 * staged multi-select (`selectedIds`/`onToggle`) for a caller that submits
	 * the selection as a batch instead of acting per click. */
	export let selectionMode: 'assignments' | 'single' | 'multi' = 'assignments';
	export let selectedModelId: string | null = null;
	export let allowClear: boolean = false;
	export let onSelect: ((model: any) => void) | undefined = undefined;
	export let onClear: (() => void) | undefined = undefined;
	export let selectedIds: string[] = [];
	export let onToggle: ((modelId: string) => void) | undefined = undefined;
	/** `external`: filter/paginate a caller-supplied array client-side instead
	 * of hitting `api.getModels()` - for a catalog the caller already has (and
	 * that carries fields the shared model catalog doesn't, e.g. sync status). */
	export let dataSource: 'catalog' | 'external' = 'catalog';
	export let externalModels: any[] = [];
	export let externalFilter: ((model: any) => boolean) | undefined = undefined;

	// Internal state - the ones below re-exported as bindable props so an
	// `external` caller can read (and reset) what's currently applied.
	let models: any[] = [];
	let totalCount = 0;
	let loading = true;
	export let currentPage = 1;
	export let pageSize = 24;
	export let searchQuery = '';
	export let selectedType = 'all';
	let modelTypes: string[] = [];
	type ViewFilter = 'all' | 'assigned' | 'unassigned';
	let viewFilter: ViewFilter = 'all';
	let searchTimeout: ReturnType<typeof setTimeout> | null = null;
	let searchInputValue = '';

	const viewOptions: { key: ViewFilter; label: string }[] = [
		{ key: 'all', label: 'All' },
		{ key: 'assigned', label: 'Assigned' },
		{ key: 'unassigned', label: 'Unassigned' }
	];

	onMount(async () => {
		if (dataSource === 'external') return;
		await Promise.all([loadModels(), loadModelTypes()]);
	});

	// `external` mode is driven entirely by the reactive block below - the
	// handlers below still flip `currentPage`/`searchQuery`/`selectedType`
	// and call this, which is then a no-op.
	$: if (dataSource === 'external') {
		modelTypes = [...new Set(externalModels.map((m) => m.model_type).filter(Boolean))].sort();
		const q = searchQuery.trim().toLowerCase();
		const filtered = externalModels.filter((m) => {
			if (selectedType !== 'all' && m.model_type !== selectedType) return false;
			if (externalFilter && !externalFilter(m)) return false;
			if (q && !(m.filename ?? '').toLowerCase().includes(q)) return false;
			return true;
		});
		totalCount = filtered.length;
		const start = (currentPage - 1) * pageSize;
		models = filtered.slice(start, start + pageSize);
		loading = false;
	}

	async function loadModels() {
		if (dataSource === 'external') return;
		try {
			loading = true;
			const params: Parameters<typeof api.getModels>[0] = {
				all_models: selectionMode === 'assignments',
				limit: pageSize,
				offset: (currentPage - 1) * pageSize,
				search: searchQuery || undefined,
				model_type: selectedType === 'all' ? undefined : selectedType,
				include_tags: true
			};
			if (selectionMode === 'assignments' && viewFilter !== 'all') {
				params.assignment_filter = viewFilter;
				if (assignedUserId) params.assigned_user_id = assignedUserId;
				if (assignedGroupId) params.assigned_group_id = assignedGroupId;
			}
			const response = await api.getModels(params);
			if (response.success && response.data) {
				models = response.data.models || [];
				totalCount = response.data.total || 0;
			}
		} catch (error) {
			logger.error('Failed to load models:', error);
		} finally {
			loading = false;
		}
	}

	async function loadModelTypes() {
		try {
			const response = await api.getModelTypes({ user_scoped: selectionMode === 'single' });
			if (response.success && response.data) {
				modelTypes = (response.data.types || []).map((t: any) => t.type);
			}
		} catch (error) {
			logger.error('Failed to load model types:', error);
		}
	}

	function handleSearchInput(value: string) {
		searchInputValue = value;
		if (searchTimeout) clearTimeout(searchTimeout);
		searchTimeout = setTimeout(() => {
			searchQuery = searchInputValue;
			currentPage = 1;
			loadModels();
		}, 300);
	}

	function handleTypeChange(type: string) {
		selectedType = type;
		currentPage = 1;
		loadModels();
	}

	function goToPage(page: number) {
		currentPage = page;
		loadModels();
	}

	function handleViewFilterChange(filter: ViewFilter) {
		viewFilter = filter;
		currentPage = 1;
		loadModels();
	}

	function handleCardClick(model: any) {
		if (processingModelId === model.id) return;
		if (selectionMode === 'single') {
			onSelect?.(model);
			return;
		}
		if (selectionMode === 'multi') {
			onToggle?.(model.id);
			return;
		}
		if (assignedModelIds.includes(model.id)) {
			onUnassign?.(model.id);
		} else {
			onAssign?.(model.id);
		}
	}

	function getDisplayName(model: any): string {
		// modelDisplayName() never falls back to the bare id - keep that as the
		// last resort here, since this grid has nothing else to show per card.
		return modelDisplayName(model) || model.id;
	}

	function formatFileSize(bytes: number | null | undefined): string {
		if (!bytes) return '';
		if (bytes >= 1024 * 1024 * 1024) {
			return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
		}
		if (bytes >= 1024 * 1024) {
			return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
		}
		return (bytes / 1024).toFixed(1) + ' KB';
	}

	function formatDate(isoString: string | null | undefined): string {
		if (!isoString) return '';
		try {
			const d = new Date(isoString);
			return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
		} catch {
			return '';
		}
	}


	function getModelMedia(model: any): any | null {
		const files = model.files?.filter((f: any) => f.file_type === 'image' || f.file_type === 'thumbnail' || f.file_type === 'video') || [];
		return files.length > 0 ? files[0] : null;
	}

	$: assignedOnPage = models.filter((m) => assignedModelIds.includes(m.id)).length;
	$: totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
	$: startItem = totalCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;
	$: endItem = Math.min(currentPage * pageSize, totalCount);

	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: showAdminVisibilityNote = isAdmin && selectionMode === 'assignments';
</script>

<div class="flex flex-col min-h-0">
	<!-- Search + filters + summary -->
	<div class="px-6 pt-4 pb-4 border-b border-line space-y-3">
		<input
			type="text"
			class="input text-sm"
			placeholder="Search models by name..."
			value={searchInputValue}
			on:input={(e) => handleSearchInput(e.currentTarget.value)}
		/>

		<div class="flex items-center gap-4 flex-wrap">
			<!-- Type filter pills -->
			<div class="flex flex-wrap items-center gap-0.5 rounded p-0.5 bg-surface-2/50">
				<button
					class="px-3 py-1 rounded text-xs font-medium transition-colors {selectedType === 'all' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50'}"
					on:click={() => handleTypeChange('all')}
				>
					All Types
				</button>
				{#each modelTypes as type}
					<button
						class="px-3 py-1 rounded text-xs font-medium transition-colors {selectedType === type ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50'}"
						on:click={() => handleTypeChange(type)}
					>
						{type}
					</button>
				{/each}
			</div>

			<!-- Separator -->
			<div class="w-px h-5 bg-line hidden sm:block"></div>

			{#if selectionMode === 'assignments'}
				<!-- View toggle -->
				<div class="flex items-center gap-0.5 rounded p-0.5 bg-surface-2/50">
					{#each viewOptions as opt}
						<button
							class="px-3 py-1 rounded text-xs font-medium transition-colors {viewFilter === opt.key ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50'}"
							on:click={() => handleViewFilterChange(opt.key)}
						>
							{opt.label}
						</button>
					{/each}
				</div>
			{/if}

			<slot name="extraFilters" />
		</div>

		{#if showAdminVisibilityNote}
			<p class="flex items-center gap-1 text-2xs text-fg-subtle">
				<Icon name="info" className="w-3 h-3 flex-shrink-0" />
				Admin account: all models are visible. Highlighted cards are the ones explicitly assigned.
			</p>
		{/if}

		<p class="text-xs text-fg-muted">
			{#if totalCount > 0}
				Showing {startItem}–{endItem} of {totalCount} models
				{#if selectionMode === 'assignments' && assignedOnPage > 0}
					<span class="inline-flex items-center px-1.5 py-0.5 ml-1 rounded text-2xs font-medium bg-signal/10 text-signal border border-signal/25">
						{assignedOnPage} assigned on page
					</span>
				{/if}
			{:else}
				No models found
			{/if}
		</p>
	</div>

	<!-- Content area -->
	<div class="flex-1 overflow-y-auto min-h-0 px-6 py-4">
		{#if loading}
			<div class="flex items-center justify-center py-16">
				<Spinner size="md" />
			</div>
		{:else if models.length === 0}
			<div class="text-center py-12">
				<Icon name="folder" className="w-12 h-12 text-fg-subtle mx-auto mb-3" />
				<p class="text-fg-muted text-sm">No models match current filters</p>
			</div>
		{:else}
			<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
				{#each models as model (model.id)}
					{@const assigned = selectionMode === 'single' ? selectedModelId === model.id : selectionMode === 'multi' ? selectedIds.includes(model.id) : assignedModelIds.includes(model.id)}
					{@const media = getModelMedia(model)}
					{@const processing = processingModelId === model.id}
					<div
						class="relative cursor-pointer rounded-lg overflow-hidden border-2 transition-all duration-150
							{assigned ? 'ring-2 ring-signal bg-signal/10 border-transparent' : 'border-line hover:border-line-strong'}
							{processing ? 'opacity-60 pointer-events-none' : ''}"
						on:click={() => handleCardClick(model)}
						on:keydown={(e) => e.key === 'Enter' && handleCardClick(model)}
						role="button"
						tabindex="0"
					>
						<!-- Image / Placeholder -->
						<div class="relative aspect-square overflow-hidden bg-surface-2">
							{#if media}
								<img
									src={mediaFileThumbnailUrl(media)}
									alt={model.filename}
									class="w-full h-full object-cover"
									loading="lazy"
								/>
							{:else}
								<div class="w-full h-full flex items-center justify-center" style={placeholderTint(getDisplayName(model))}>
									<Icon name="model" className="h-8 w-8 text-fg-subtle" />
								</div>
							{/if}

							<!-- Checkmark overlay -->
							{#if assigned}
								<div class="absolute top-1.5 right-1.5 bg-signal text-white rounded p-0.5 shadow-raised">
									<Icon name="check" className="w-3.5 h-3.5" strokeWidth={3} />
								</div>
							{/if}

							<!-- Processing spinner -->
							{#if processing}
								<div class="absolute inset-0 bg-black/30 flex items-center justify-center">
									<Spinner size="sm" />
								</div>
							{/if}

							<!-- Type badge -->
							<div class="absolute top-1.5 left-1.5">
								<span class="px-1.5 py-0.5 rounded text-2xs font-medium font-mono uppercase bg-black/50 text-white backdrop-blur-sm">
									{model.model_type}
								</span>
							</div>
						</div>

						<!-- Info below card -->
						<div class="px-2 py-1.5 bg-surface-1 space-y-0.5">
							<p class="text-xs font-medium text-fg truncate" title={getDisplayName(model)}>
								{getDisplayName(model)}
							</p>
							{#if model.filename && model.filename !== getDisplayName(model)}
								<p class="text-2xs text-fg-muted truncate" title={model.filename}>
									{model.filename}
								</p>
							{/if}
							<div class="flex items-center gap-1.5 text-2xs text-fg-subtle font-mono tabular-nums flex-wrap">
								{#if model.file_size}
									<span>{formatFileSize(model.file_size)}</span>
								{/if}
								{#if model.indexed_at}
									<span class="w-px h-2.5 bg-line"></span>
									<span title="Indexed at">{formatDate(model.indexed_at)}</span>
								{/if}
							</div>
							<slot name="cardExtra" {model} />
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Footer with pagination -->
	<div class="px-6 py-3 border-t border-line bg-surface-2 flex items-center justify-between">
		<div class="flex items-center gap-3">
			<p class="text-xs text-fg-muted">
				{selectionMode === 'single'
					? 'Click a model to select it'
					: selectionMode === 'multi'
						? 'Click a model to select or deselect it'
						: 'Click a model to assign/unassign'}
			</p>
			{#if selectionMode === 'single' && allowClear}
				<button
					type="button"
					class="rounded px-2.5 py-1 text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
					on:click={() => onClear?.()}
				>
					All models
				</button>
			{/if}
		</div>
		{#if totalPages > 1}
			<Pagination {currentPage} {totalPages} size="sm" onPageChange={goToPage} />
		{/if}
	</div>
</div>
