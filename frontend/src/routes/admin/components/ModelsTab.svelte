<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { isAxiosError } from 'axios';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { api } from '$lib/services/api/index';
	import { getModelsDictionary, getEnabledBackends, getModelAssignmentSummary, type AssignmentSummary } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { formatTagUsageError } from '$lib/utils/tagUsage';
	import type { TagUsageRef } from '$lib/types/api';
	import type { ModelIndexResult, UnindexedModelsCount } from '$lib/services/api/models';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import AdminModelDetailsModal from '$lib/components/modals/AdminModelDetailsModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Badge, Spinner, EmptyState, Pagination, SegmentedControl } from '$lib/components/ui';
	import AdminTabShell from './AdminTabShell.svelte';
	import AdminFilterBar from './AdminFilterBar.svelte';
	import AttributesTab from './AttributesTab.svelte';

	type View = 'models' | 'attributes';

	// 'attributes' used to be its own top-level admin tab; it now lives here as
	// a second view, deep-linked the same way LLMAssistantTab's sessions view is.
	$: view = (($page.url.searchParams.get('view') as View) || 'models') === 'attributes'
		? 'attributes'
		: 'models';

	function setView(next: View) {
		if (next === view) return;
		const url = new URL($page.url);
		url.searchParams.set('tab', 'models');
		if (next === 'models') {
			url.searchParams.delete('view');
		} else {
			url.searchParams.set('view', next);
		}
		void goto(url, { keepFocus: true, noScroll: true });
	}

	interface Provider {
		id: string;
		name: string;
		initialized: boolean;
		capabilities: string[];
	}

	interface ModelListItem {
		id: string;
		filename: string;
		[key: string]: unknown;
	}

	interface ModelTypeInfo {
		type: string;
		directory: string;
		count: number;
		size_bytes: number;
		size_mb: number;
		size_gb: number;
	}

	interface ModelTag {
		id: string;
		name: string;
		type: string;
		user_id?: string | null;
		created_at?: string;
		usage_count?: number;
		model_count?: number | null;
		generation_count?: number | null;
	}

	let models: ModelListItem[] = [];
	let modelTypes: ModelTypeInfo[] = [];
	let availableModelTypes: string[] = []; // From dictionary endpoint
	let providers: Provider[] = [];
	let selectedProviderId: string | null = null;
	let loading = true;
	let selectedType = 'all';
	let selectedTags: string[] = [];
	let availableTags: ModelTag[] = [];
	let searchQuery = '';
	let tagSearchQuery = '';
	let isTagDropdownOpen = false;
	let sortBy = 'indexed_at';
	let sortOrder = 'desc';
	let indexing = false;
	let indexingStats: ModelIndexResult | null = null;
	// Files sitting in models/<type>/ that no admin has indexed yet - surfaced next
	// to the Index Models button so a manual drop-in doesn't go unnoticed.
	let unindexedCount: UnindexedModelsCount | null = null;
	let fetchingProviderInfo = false;
	let isProviderDropdownOpen = false;
	let currentPage = 1;
	let pageSize = 30;
	let totalCount = 0;
	// Whether any backend has ever been indexed (see docs/models.md) - gates whether an
	// empty `model.backend_ids` reads as "unavailable" vs. "unknown" on the cards below.
	let availabilityIndexed = false;
	let backendNames: Record<string, string> = {};
	let assignmentSummary: AssignmentSummary = {};

	// Modal states
	let selectedModelId: string | null = null;
	let isModelDetailsOpen = false;

	let tagDropdownRef: HTMLElement;
	let providerDropdownRef: HTMLElement;

	// Computed: get the selected provider object
	$: selectedProvider = providers.find(p => p.id === selectedProviderId) || null;
	$: hasInitializedProviders = providers.some(p => p.initialized);

	onMount(() => {
		const handleClickOutside = (event: MouseEvent) => {
			if (tagDropdownRef && !tagDropdownRef.contains(event.target as Node)) {
				isTagDropdownOpen = false;
			}
			if (providerDropdownRef && !providerDropdownRef.contains(event.target as Node)) {
				isProviderDropdownOpen = false;
			}
		};

		loadData().then(() => {
			document.addEventListener('mousedown', handleClickOutside);
		});

		return () => {
			document.removeEventListener('mousedown', handleClickOutside);
		};
	});

	// Reload models when filters change
	$: if (!loading) {
		loadModels();
	}

	$: {
		// Trigger reload on these dependencies
		currentPage;
		selectedType;
		searchQuery;
		selectedTags;
		sortBy;
		sortOrder;
		if (!loading) loadModels();
	}

	async function loadData() {
		try {
			loading = true;
			await Promise.all([
				loadModels(),
				loadModelTypes(),
				loadProviders(),
				loadAvailableTags(),
				loadModelTypesDictionary(),
				loadBackendNames(),
				loadAssignmentSummary(),
				loadUnindexedCount()
			]);
		} catch (error) {
			logger.error('Error loading data:', error);
		} finally {
			loading = false;
		}
	}

	async function loadModels() {
		try {
			const response = await api.getModels({
				model_type: selectedType === 'all' ? undefined : selectedType,
				search: searchQuery || undefined,
				tag_ids: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
				sort_by: sortBy,
				sort_order: sortOrder,
				include_tags: true,
				limit: pageSize,
				offset: (currentPage - 1) * pageSize,
				all_models: true
			});
			if (response.success) {
				models = response.data?.models || [];
				totalCount = response.data?.total || 0;
				availabilityIndexed = response.data?.availability_indexed ?? false;
			}
		} catch (error) {
			logger.error('Error loading models:', error);
		}
	}

	async function loadAssignmentSummary() {
		try {
			const response = await getModelAssignmentSummary();
			if (response.success && response.data) {
				assignmentSummary = response.data;
			}
		} catch (error) {
			logger.error('Error loading model assignment summary:', error);
		}
	}

	async function loadBackendNames() {
		try {
			const response = await getEnabledBackends();
			if (response.success && response.data) {
				backendNames = Object.fromEntries(response.data.map((b) => [b.id, b.name]));
			}
		} catch (error) {
			logger.error('Error loading backends:', error);
		}
	}

	async function loadModelTypes() {
		try {
			const response = await api.getModelTypes();
			if (response.success) {
				modelTypes = response.data?.types || [];
			}
		} catch (error) {
			logger.error('Error loading model types:', error);
		}
	}

	async function loadProviders() {
		try {
			const response = await api.getProviders();
			if (response.success) {
				providers = response.data || [];
				// Auto-select first initialized provider if none selected
				if (!selectedProviderId && providers.length > 0) {
					const initializedProvider = providers.find(p => p.initialized);
					selectedProviderId = initializedProvider?.id || providers[0]?.id || null;
				}
			} else {
				providers = [];
			}
		} catch (error) {
			logger.error('Error loading providers:', error);
			providers = [];
		}
	}

	async function loadAvailableTags() {
		try {
			const response = await api.getTags('MODEL');
			if (response.success) {
				availableTags = response.data?.tags || [];
			}
		} catch (error) {
			logger.error('Error loading available tags:', error);
		}
	}

	async function loadModelTypesDictionary() {
		try {
			const response = await getModelsDictionary();
			if (response.success) {
				availableModelTypes = response.data?.models || [];
			}
		} catch (error) {
			logger.error('Error loading model types dictionary:', error);
		}
	}

	async function loadUnindexedCount() {
		try {
			const response = await api.getUnindexedModelsCount();
			if (response.success && response.data) {
				unindexedCount = response.data;
			}
		} catch (error) {
			logger.error('Error loading unindexed models count:', error);
		}
	}

	async function handleIndexModels() {
		try {
			indexing = true;
			indexingStats = null;
			const response = await api.indexModels();
			if (response.success && response.data) {
				indexingStats = response.data;
				setTimeout(() => {
					loadData();
					indexing = false;
					indexingStats = null;
				}, 3000);
			} else {
				indexing = false;
			}
		} catch (error) {
			logger.error('Error indexing models:', error);
			indexing = false;
			indexingStats = null;
		}
	}

	async function handleFetchProviderInfo(providerId: string, forceRefresh: boolean = false) {
		if (!providerId) return;

		try {
			fetchingProviderInfo = true;
			isProviderDropdownOpen = false;
			const response = await api.fetchProviderInfo(providerId, undefined, forceRefresh);
			if (response.success) {
				setTimeout(() => {
					loadData();
					fetchingProviderInfo = false;
				}, 3000);
			} else {
				// Show error to user
				logger.error('Error fetching provider info:', response.message);
				fetchingProviderInfo = false;
			}
		} catch (error) {
			logger.error('Error fetching provider info:', error);
			fetchingProviderInfo = false;
		}
	}

	async function handleDeleteModel(modelId: string, name: string) {
		if (await confirmDialog({
			title: `Are you sure you want to remove "${name}" from the index?`,
			message: 'This will not delete the file.',
			variant: 'danger'
		})) {
			try {
				const response = await api.deleteModel(modelId);
				if (response.success) {
					await loadModels();
				}
			} catch (error) {
				logger.error('Error deleting model:', error);
			}
		}
	}

	async function handleCleanup() {
		if (await confirmDialog({
			title: 'Remove models from index',
			message: 'Remove models from index that no longer exist on disk?',
			variant: 'danger'
		})) {
			try {
				const response = await api.cleanupDeletedModels();
				if (response.success) {
					await loadData();
				}
			} catch (error) {
				logger.error('Error cleaning up models:', error);
			}
		}
	}

	function openModelDetails(modelId: string) {
		selectedModelId = modelId;
		isModelDetailsOpen = true;
	}

	function closeModelDetails() {
		isModelDetailsOpen = false;
		selectedModelId = null;
		// Edits made in the modal (preview, description, tags, assignments, ...) aren't
		// pushed back into the list rows, so refetch to reflect them on the cards.
		loadModels();
		loadAssignmentSummary();
	}

	function handleToggleTag(tagId: string) {
		if (selectedTags.includes(tagId)) {
			selectedTags = selectedTags.filter((id) => id !== tagId);
		} else {
			selectedTags = [...selectedTags, tagId];
		}
		currentPage = 1;
	}

	function handleRemoveTag(tagId: string) {
		selectedTags = selectedTags.filter((id) => id !== tagId);
		currentPage = 1;
	}

	let deletingTagId: string | null = null;

	async function handleDeleteTag(tag: ModelTag, event: MouseEvent) {
		event.stopPropagation();
		if (!(await confirmDialog({
			title: `Delete tag "${tag.name}"?`,
			message: 'This cannot be undone.',
			variant: 'danger'
		}))) return;
		deletingTagId = tag.id;
		try {
			const response = await api.deleteTag(tag.id);
			if (!response.success) throw new Error(response.message || 'Could not delete tag');
			availableTags = availableTags.filter((t) => t.id !== tag.id);
			selectedTags = selectedTags.filter((id) => id !== tag.id);
			toasts.success(`Tag "${tag.name}" deleted`);
		} catch (error: unknown) {
			if (isAxiosError<{ used_by?: TagUsageRef[] }>(error) && error.response?.status === 409) {
				toasts.error(formatTagUsageError(tag.name, error.response.data?.used_by));
			} else {
				logger.error('Failed to delete tag:', error);
				toasts.error(error instanceof Error ? error.message : 'Could not delete tag');
			}
		} finally {
			deletingTagId = null;
		}
	}

	// `const` is evaluated once at init, before `availableTags` or `totalCount` have
	// loaded — the tag filter never filtered and the pager always read "/ 0".
	$: filteredTags = availableTags.filter((tag) =>
		tag.name.toLowerCase().includes(tagSearchQuery.toLowerCase())
	);

	$: totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
	$: activeFilterCount = Number(!!searchQuery.trim()) + Number(selectedType !== 'all');
	function clearPrimaryFilters() {
		searchQuery = '';
		selectedType = 'all';
		currentPage = 1;
	}
</script>

<div class="space-y-4">
	<SegmentedControl
		items={[
			{ id: 'models', label: 'Models', icon: 'cube' },
			{ id: 'attributes', label: 'Attributes', icon: 'sliders' }
		]}
		selected={view}
		onSelect={(id) => setView(id as View)}
		ariaLabel="Models views"
	/>

	{#if view === 'attributes'}
		<AttributesTab />
	{:else}
	<AdminTabShell title="Models" icon="cube" counts={[{ label: totalCount === 1 ? 'model' : 'models', value: totalCount }]}>
	{#snippet actions()}
		<Button variant="secondary" size="sm" icon="refresh" onclick={handleIndexModels} disabled={indexing}>
			{#if indexing}
				{#if indexingStats}
					Indexed {indexingStats.indexed}/{indexingStats.new_files} new ({indexingStats.failed} failed)
				{:else}
					Indexing...
				{/if}
			{:else}
				Index Models
			{/if}
		</Button>
		{#if !indexing && unindexedCount && unindexedCount.total > 0}
			<Tooltip text={`${unindexedCount.total} file${unindexedCount.total === 1 ? '' : 's'} on disk not yet indexed`}>
				<Badge variant="signal">{unindexedCount.total}</Badge>
			</Tooltip>
		{/if}

		<!-- Provider Selector and Fetch Button -->
		{#if providers.length > 0}
			<div bind:this={providerDropdownRef} class="relative inline-flex rounded overflow-hidden border border-line-strong">
				<select
					bind:value={selectedProviderId}
					class="px-2.5 py-1.5 bg-surface-3 text-fg text-xs focus:outline-none cursor-pointer hover:bg-line-hover transition-colors disabled:opacity-50"
					disabled={fetchingProviderInfo}
				>
					{#each providers as provider}
						<option value={provider.id}>
							{provider.name} {provider.initialized ? '' : '(not configured)'}
						</option>
					{/each}
				</select>
				<button
					class="px-3 py-1.5 bg-surface-3 text-fg hover:bg-line-hover flex items-center gap-1.5 text-xs disabled:opacity-50 transition-colors border-l border-line-strong"
					on:click={() => selectedProviderId && handleFetchProviderInfo(selectedProviderId, false)}
					disabled={fetchingProviderInfo || !selectedProvider?.initialized}
				>
					<Icon name="refresh" className="w-3.5 h-3.5" />
					{fetchingProviderInfo ? 'Fetching...' : 'Fetch'}
				</button>
				<button
					class="px-1.5 py-1.5 bg-surface-3 text-fg hover:bg-line-hover border-l border-line-strong disabled:opacity-50 transition-colors"
					on:click={() => (isProviderDropdownOpen = !isProviderDropdownOpen)}
					disabled={fetchingProviderInfo || !selectedProvider?.initialized}
				>
					<Icon name="chevron-down" className="w-3.5 h-3.5" />
				</button>
				{#if isProviderDropdownOpen && selectedProviderId}
					<div class="absolute top-full right-0 mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating z-50 min-w-[180px]">
						<button
							class="w-full text-left px-3 py-2 text-xs hover:bg-surface-2 rounded-t-lg transition-colors"
							on:click={() => selectedProviderId && handleFetchProviderInfo(selectedProviderId, false)}
						>
							<div class="font-medium text-fg">Fetch Missing Only</div>
							<div class="text-2xs text-fg-subtle">Faster - only fetch missing info</div>
						</button>
						<button
							class="w-full text-left px-3 py-2 text-xs hover:bg-surface-2 rounded-b-lg transition-colors"
							on:click={() => selectedProviderId && handleFetchProviderInfo(selectedProviderId, true)}
						>
							<div class="font-medium text-fg">Force Refresh All</div>
							<div class="text-2xs text-fg-subtle">Slower - refresh all models</div>
						</button>
					</div>
				{/if}
			</div>
		{:else}
			<span class="text-xs text-fg-subtle">No providers configured</span>
		{/if}

		<Button variant="secondary" size="sm" icon="trash" onclick={handleCleanup}>
			Cleanup
		</Button>
	{/snippet}
	</AdminTabShell>

	<!-- Per-backend numbers (indexed models / size) live on each backend's own Stats tab in Backends. -->
	{#snippet modelsSearch()}
			<div class="relative">
				<Icon name="search" className="h-4 w-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
				<input
					type="text"
					placeholder="Search by filename..."
					bind:value={searchQuery}
					on:input={() => (currentPage = 1)}
					class="input pl-10 w-full"
				/>
			</div>
		{/snippet}
		{#snippet modelsFilters()}
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Type</span>
				<select
					bind:value={selectedType}
					on:change={() => (currentPage = 1)}
					class="input w-48"
				>
					<option value="all">All Types</option>
					{#each modelTypes as type}
						<option value={type.type}>{type.type.toUpperCase()} ({type.count})</option>
					{/each}
				</select>
			</div>
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Sort</span>
				<select
					bind:value={sortBy}
					on:change={() => (currentPage = 1)}
					class="input w-40"
				>
					<option value="indexed_at">Indexed Date</option>
					<option value="modified_at">Modified Date</option>
					<option value="filename">Filename</option>
					<option value="file_size">File Size</option>
				</select>
				<select
					bind:value={sortOrder}
					on:change={() => (currentPage = 1)}
					class="input w-32"
				>
					<option value="desc">Newest First</option>
					<option value="asc">Oldest First</option>
				</select>
			</div>
		{/snippet}

		<AdminFilterBar
			search={modelsSearch}
			filters={modelsFilters}
			activeCount={activeFilterCount}
			onClear={clearPrimaryFilters}
		/>

		<!-- Tags are a richer stateful control (its own dropdown + selected-tag
		     chips) than the select/text controls AdminFilterBar's popover
		     duplicates safely, so it stays its own row rather than joining the
		     collapse group. -->
		<div class="bg-surface-1 rounded-lg border border-line shadow-raised p-4 mb-6">
			<div class="flex items-start gap-4">
				<div bind:this={tagDropdownRef} class="relative w-64">
					<input
						type="text"
						placeholder="Filter by tags..."
						bind:value={tagSearchQuery}
						on:focus={() => (isTagDropdownOpen = true)}
						class="input"
					/>

					{#if isTagDropdownOpen}
						<div
							class="absolute top-full left-0 right-0 z-50 mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-64 overflow-auto"
						>
							{#if filteredTags.length === 0}
								<div class="p-2 text-sm text-fg-subtle text-center">No tags found</div>
							{:else}
								{#each filteredTags as tag}
									<div
										class="w-full flex items-center gap-1 pr-1 hover:bg-surface-3 {selectedTags.includes(tag.id)
											? 'bg-surface-2 text-fg'
											: ''}"
									>
										<button
											type="button"
											class="flex-1 min-w-0 text-left px-3 py-2 text-sm flex items-center justify-between"
											on:click={() => handleToggleTag(tag.id)}
										>
											<span class="truncate">{tag.name}</span>
											<span class="text-fg-subtle ml-2 flex-shrink-0">
												{tag.model_count ? `(${tag.model_count})` : ''}
											</span>
										</button>
										<IconButton
											icon="trash"
											label={`Delete tag ${tag.name}`}
											size="sm"
											class="flex-shrink-0 hover:text-danger"
											disabled={deletingTagId === tag.id}
											onclick={(e) => handleDeleteTag(tag, e)}
										/>
									</div>
								{/each}
							{/if}
						</div>
					{/if}
				</div>

				<!-- Selected Tags Display -->
				{#if selectedTags.length > 0}
					<div class="flex-1 flex flex-wrap gap-1 items-center">
						<span class="text-sm text-fg-muted mr-2">Selected:</span>
						{#each selectedTags as tagId}
							{@const tag = availableTags.find((t) => t.id === tagId)}
							{#if tag}
								<Badge variant="neutral">
									{tag.name}
									<button on:click={() => handleRemoveTag(tagId)} aria-label="Remove tag">
										<Icon name="close" className="w-3 h-3" />
									</button>
								</Badge>
							{/if}
						{/each}
					</div>
				{/if}
			</div>
		</div>

		<!-- Models Grid -->
		{#if loading}
			<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 gap-6">
				{#each Array.from({ length: pageSize }) as _, i}
					<div class="group">
						<div class="relative rounded-lg overflow-hidden bg-surface-1 shadow-raised border border-line-strong">
							<div class="aspect-[4/5] w-full bg-surface-2 animate-pulse"></div>
							<div class="p-3 space-y-2">
								<div class="h-3 bg-surface-3 rounded animate-pulse"></div>
								<div class="h-2 bg-surface-2 rounded animate-pulse w-3/4"></div>
							</div>
						</div>
					</div>
				{/each}
			</div>
		{:else if models.length > 0}
			<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 gap-6">
				{#each models as model (model.id)}
					<ModelCard
						{model}
						showTechnical
						showManagementActions
						{availabilityIndexed}
						{backendNames}
						unassigned={!(assignmentSummary[model.id]?.assignment_count || 0) && !(assignmentSummary[model.id]?.group_count || 0)}
						on:view={(e) => openModelDetails(e.detail.id)}
						on:delete={(e) => handleDeleteModel(e.detail.id, e.detail.name || e.detail.filename)}
					/>
				{/each}
			</div>
		{:else}
			<EmptyState
				icon="model"
				title={searchQuery || selectedType !== 'all' || selectedTags.length > 0
					? 'No models match your filters'
					: 'No models indexed yet'}
				description={searchQuery || selectedType !== 'all' || selectedTags.length > 0
					? "Try adjusting your search criteria or filters to find what you're looking for."
					: 'Index your models to get started. They will appear in this gallery once found.'}
			>
				{#snippet actions()}
					{#if searchQuery || selectedType !== 'all' || selectedTags.length > 0}
						<Button
							variant="secondary"
							icon="close"
							onclick={() => {
								searchQuery = '';
								selectedType = 'all';
								selectedTags = [];
							}}
						>
							Clear All Filters
						</Button>
					{:else}
						<Button variant="primary" icon="refresh" onclick={handleIndexModels}>
							Index Models
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}

		<!-- Pagination -->
		{#if totalCount > pageSize && models.length > 0}
			<div class="flex justify-center items-center mt-8">
				<Pagination {currentPage} {totalPages} onPageChange={(page) => (currentPage = page)} />
			</div>
		{/if}

	<!-- Model Details Modal -->
	<AdminModelDetailsModal
		isOpen={isModelDetailsOpen}
		modelId={selectedModelId}
		onClose={closeModelDetails}
	/>
	{/if}
</div>
