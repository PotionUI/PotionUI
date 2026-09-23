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
	import type { UnindexedModelsCount } from '$lib/services/api/models';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import AdminModelDetailsModal from '$lib/components/modals/AdminModelDetailsModal.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import AssignmentCard from '$lib/components/assignment/AssignmentCard.svelte';
	import {
		createModelAssignmentAdapter,
		createBulkModelAssignmentAdapter
	} from '$lib/components/assignment/modelAssignmentAdapter';
	import type { AssignmentAdapter } from '$lib/components/assignment/types';
	import { isModelUnassigned, applyAssignmentSummaryChange } from '$lib/components/assignment/assignmentSummary';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Badge, Spinner, EmptyState, Pagination, SegmentedControl } from '$lib/components/ui';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryFilterChipRow from '$lib/components/library/LibraryFilterChipRow.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import AttributesTab from './AttributesTab.svelte';
	import {
		DEFAULT_MODELS_FILTERS,
		MODELS_SORT_OPTIONS,
		clearAllModelsFilters,
		clearModelsFilterChip,
		modelsFilterActiveCount,
		modelsFilterChips,
		modelsFiltersFromSearchParams,
		modelsFiltersToSearchParams,
		modelsHasActiveFilters,
		modelsSortParams,
		type ModelsFilters,
		type ModelsSortBy
	} from './modelsFilters';

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
	let loading = true;
	let availableTags: ModelTag[] = [];
	let tagSearchQuery = '';
	let isTagDropdownOpen = false;
	let unindexedCount: UnindexedModelsCount | null = null;
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

	let selectionMode = false;
	let selectedModelIds: string[] = [];

	let assigningModel: ModelListItem | null = null;
	let isAssignModalOpen = false;

	let bulkModelIds: string[] = [];
	let bulkAdapter: AssignmentAdapter | null = null;
	let isBulkAssignOpen = false;
	let bulkProgress = { done: 0, total: 0 };
	$: bulkBusy = bulkProgress.total > 0 && bulkProgress.done < bulkProgress.total;

	let tagDropdownRef: HTMLElement;


	onMount(() => {
		const handleClickOutside = (event: MouseEvent) => {
			if (tagDropdownRef && !tagDropdownRef.contains(event.target as Node)) {
				isTagDropdownOpen = false;
			}
		};

		loadData().then(() => {
			document.addEventListener('mousedown', handleClickOutside);
		});

		return () => {
			document.removeEventListener('mousedown', handleClickOutside);
		};
	});

	$: filters = modelsFiltersFromSearchParams($page.url.searchParams);

	// Reload models when the URL-derived filters change, but only while the
	// models grid (not the embedded Attributes view) is showing.
	$: {
		filters;
		if (!loading && view === 'models') loadModels();
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function buildModelsUrl(next: ModelsFilters): string {
		const params = new URLSearchParams($page.url.searchParams);
		for (const key of ['q', 'type', 'tags', 'sort_by']) params.delete(key);
		for (const [key, value] of modelsFiltersToSearchParams(next)) params.set(key, value);
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	function updateFilters(next: ModelsFilters) {
		currentPage = 1;
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildModelsUrl(next), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	async function loadData() {
		try {
			loading = true;
			await Promise.all([
				loadModels(),
				loadModelTypes(),
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
			const { sort_by, sort_order } = modelsSortParams(filters.sortBy);
			const response = await api.getModels({
				model_type: filters.type === 'all' ? undefined : filters.type,
				search: filters.q || undefined,
				tag_ids: filters.tags.length > 0 ? filters.tags.join(',') : undefined,
				sort_by,
				sort_order,
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

	function toggleModelSelect(model: ModelListItem) {
		const isSelected = selectedModelIds.includes(model.id);
		selectedModelIds = isSelected
			? selectedModelIds.filter((id) => id !== model.id)
			: [...selectedModelIds, model.id];
		selectionMode = selectedModelIds.length > 0;
	}

	function selectAllModelsOnPage() {
		selectedModelIds = models.map((m) => m.id);
		selectionMode = selectedModelIds.length > 0;
	}

	function clearModelSelection() {
		selectedModelIds = [];
		selectionMode = false;
	}

	function openAssignModal(model: ModelListItem) {
		assigningModel = model;
		isAssignModalOpen = true;
	}

	function closeAssignModal() {
		isAssignModalOpen = false;
		assigningModel = null;
		loadAssignmentSummary();
	}

	function handleAssignmentChanged(modelId: string, event: CustomEvent<{ userCount: number; groupCount: number }>) {
		assignmentSummary = applyAssignmentSummaryChange(assignmentSummary, modelId, event.detail);
	}

	function openBulkAssignModal() {
		bulkModelIds = [...selectedModelIds];
		bulkProgress = { done: 0, total: 0 };
		bulkAdapter = createBulkModelAssignmentAdapter(bulkModelIds, (done, total) => {
			bulkProgress = { done, total };
		});
		isBulkAssignOpen = true;
	}

	function closeBulkAssignModal() {
		isBulkAssignOpen = false;
		bulkAdapter = null;
		clearModelSelection();
		loadAssignmentSummary();
	}

	function handleToggleTag(tagId: string) {
		const next = filters.tags.includes(tagId)
			? filters.tags.filter((id) => id !== tagId)
			: [...filters.tags, tagId];
		updateFilters({ ...filters, tags: next });
	}

	function handleRemoveTag(tagId: string) {
		updateFilters({ ...filters, tags: filters.tags.filter((id) => id !== tagId) });
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
			if (filters.tags.includes(tag.id)) updateFilters({ ...filters, tags: filters.tags.filter((id) => id !== tag.id) });
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
	$: activeFilterCount = modelsFilterActiveCount(filters);
	$: filterChips = modelsFilterChips(filters, availableTags);
	$: hasActiveFilters = modelsHasActiveFilters(filters);
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
		{#if unindexedCount && unindexedCount.total > 0}
			<Tooltip text="Files on disk that are not indexed yet. Index them from the backend that loads them.">
				<Button variant="secondary" size="sm" icon="server" href="/admin?tab=backends">
					{unindexedCount.total} not indexed
				</Button>
			</Tooltip>
		{/if}

		<Button variant="secondary" size="sm" icon="trash" onclick={handleCleanup}>
			Cleanup
		</Button>
	{/snippet}
	</AdminTabShell>

	<div class="rounded-lg border border-line bg-surface-1 px-4 py-2.5 shadow-raised flex flex-wrap items-center gap-2">
			<LibraryFilterBar
				q={filters.q}
				onQueryChange={(value) => updateFilters({ ...filters, q: value })}
				searchPlaceholder="Search by filename…"
				sortBy={filters.sortBy}
				sortOptions={MODELS_SORT_OPTIONS}
				onSortChange={(value) => updateFilters({ ...filters, sortBy: value as ModelsSortBy })}
				filterCount={activeFilterCount}
			>
				{#snippet popover(close: () => void)}
					<FilterPopoverFrame
						label="Model filters"
						onClearAll={() => updateFilters(clearAllModelsFilters(filters))}
						onClose={close}
					>
						<div class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-medium text-fg-muted">Type</span>
							<select
								class="input"
								value={filters.type}
								on:change={(event) =>
									updateFilters({ ...filters, type: (event.currentTarget as HTMLSelectElement).value })}
							>
								<option value="all">All Types</option>
								{#each modelTypes as type (type.type)}
									<option value={type.type}>{type.type.toUpperCase()} ({type.count})</option>
								{/each}
							</select>
						</div>

						<div class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-medium text-fg-muted">Tags</span>
							<div bind:this={tagDropdownRef} class="relative">
								<input
									type="text"
									placeholder="Filter by tags…"
									bind:value={tagSearchQuery}
									on:focus={() => (isTagDropdownOpen = true)}
									class="input"
								/>

								{#if isTagDropdownOpen}
									<div
										class="absolute top-full left-0 right-0 z-50 mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-48 overflow-auto"
									>
										{#if filteredTags.length === 0}
											<div class="p-2 text-sm text-fg-subtle text-center">No tags found</div>
										{:else}
											{#each filteredTags as tag (tag.id)}
												<div
													class="w-full flex items-center gap-1 pr-1 hover:bg-surface-3 {filters.tags.includes(tag.id)
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

							{#if filters.tags.length > 0}
								<div class="flex flex-wrap gap-1.5">
									{#each filters.tags as tagId (tagId)}
										{@const tag = availableTags.find((t) => t.id === tagId)}
										{#if tag}
											<Badge variant="neutral">
												{tag.name}
												<button type="button" on:click={() => handleRemoveTag(tagId)} aria-label={`Remove tag ${tag.name}`}>
													<Icon name="close" className="w-3 h-3" />
												</button>
											</Badge>
										{/if}
									{/each}
								</div>
							{/if}
						</div>
					</FilterPopoverFrame>
				{/snippet}
			</LibraryFilterBar>
		</div>

		<LibraryFilterChipRow
			chips={filterChips}
			onRemoveChip={(key) => updateFilters(clearModelsFilterChip(filters, key))}
			onClearAll={() => updateFilters(clearAllModelsFilters(filters))}
			loadedCount={models.length}
			total={totalCount}
		/>

		{#if selectionMode}
			<div class="flex items-center gap-3 rounded-lg border border-line-strong bg-surface-2 px-4 py-2">
				<span class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">
					<span class="tabular-nums text-fg">{selectedModelIds.length}</span> selected
				</span>
				<Button variant="ghost" size="sm" onclick={selectAllModelsOnPage}>Select all on page</Button>
				<div class="ml-auto flex items-center gap-1">
					<Tooltip text="Assign access">
						<IconButton icon="group" label="Assign access" onclick={openBulkAssignModal} />
					</Tooltip>
					<IconButton icon="close" label="Clear selection" onclick={clearModelSelection} />
				</div>
			</div>
		{/if}

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
						selectable={selectionMode}
						showCheckbox={true}
						selected={selectedModelIds.includes(model.id)}
						onSelect={toggleModelSelect}
						unassigned={isModelUnassigned(assignmentSummary[model.id])}
						on:view={(e) => openModelDetails(e.detail.id)}
						on:delete={(e) => handleDeleteModel(e.detail.id, e.detail.name || e.detail.filename)}
						on:assign={(e) => openAssignModal(e.detail)}
						on:refresh={loadModels}
					/>
				{/each}
			</div>
		{:else}
			<EmptyState
				icon="model"
				title={hasActiveFilters ? 'No models match your filters' : 'No models indexed yet'}
				description={hasActiveFilters
					? "Try adjusting your search criteria or filters to find what you're looking for."
					: 'Index models from the backend that loads them. They will appear in this gallery once found.'}
			>
				{#snippet actions()}
					{#if hasActiveFilters}
						<Button variant="secondary" icon="close" onclick={() => updateFilters(DEFAULT_MODELS_FILTERS)}>
							Clear All Filters
						</Button>
					{:else}
						<Button variant="primary" icon="server" href="/admin?tab=backends">
							Open Backends
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}

		{#if totalCount > pageSize && models.length > 0}
			<div class="flex justify-center items-center mt-8">
				<Pagination
					{currentPage}
					{totalPages}
					onPageChange={(page) => {
						currentPage = page;
						loadModels();
					}}
				/>
			</div>
		{/if}

	<!-- Model Details Modal -->
	<AdminModelDetailsModal
		isOpen={isModelDetailsOpen}
		modelId={selectedModelId}
		onClose={closeModelDetails}
	/>

	{#if isAssignModalOpen && assigningModel}
		<BaseModal
			isOpen={isAssignModalOpen}
			title={`Assign access — ${modelDisplayName(assigningModel)}`}
			size="lg"
			on:close={closeAssignModal}
		>
			<div class="p-6">
				{#key assigningModel.id}
					<AssignmentCard
						adapter={createModelAssignmentAdapter(assigningModel.id)}
						resourceKey={assigningModel.id}
						resourceName={modelDisplayName(assigningModel)}
						on:changed={(e) => handleAssignmentChanged(assigningModel!.id, e)}
					/>
				{/key}
			</div>
			<svelte:fragment slot="footer">
				<ConfirmFooter confirmLabel="Done" onCancel={closeAssignModal} onConfirm={closeAssignModal} />
			</svelte:fragment>
		</BaseModal>
	{/if}

	{#if isBulkAssignOpen && bulkAdapter}
		<BaseModal
			isOpen={isBulkAssignOpen}
			title={`Assign access — ${bulkModelIds.length} model${bulkModelIds.length === 1 ? '' : 's'}`}
			size="lg"
			closeable={!bulkBusy}
			on:close={closeBulkAssignModal}
		>
			<div class="p-6">
				<AssignmentCard
					adapter={bulkAdapter}
					resourceKey={bulkModelIds.join(',')}
					resourceName={`${bulkModelIds.length} selected model${bulkModelIds.length === 1 ? '' : 's'}`}
				/>
			</div>
			<svelte:fragment slot="footer">
				<ConfirmFooter
					confirmLabel="Done"
					confirmDisabled={bulkBusy}
					summary={bulkBusy ? `${bulkProgress.done} / ${bulkProgress.total}` : undefined}
					onCancel={closeBulkAssignModal}
					onConfirm={closeBulkAssignModal}
				/>
			</svelte:fragment>
		</BaseModal>
	{/if}
	{/if}
</div>
