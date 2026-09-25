<script lang="ts">
	import { onMount } from 'svelte';
	import { isAxiosError } from 'axios';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { getEnabledBackends, getModelAssignmentSummary, type AssignmentSummary } from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { formatTagUsageError } from '$lib/utils/tagUsage';
	import { formatBytes } from '$lib/utils/format';
	import { isAvailabilityKnown } from '$lib/utils/modelAvailability';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import type { TagUsageRef } from '$lib/types/api';
	import type { UnindexedModelsCount } from '$lib/services/api/models';
	import type { AttributeDefinition } from '$lib/types/models';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
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
	import { Button, IconButton, Badge, Spinner, EmptyState } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import { DataTable, StatusCell, TablePager, pageCount, type DataTableColumn } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import AttributesList from './models/AttributesList.svelte';
	import AttributeDetailPage from './models/AttributeDetailPage.svelte';
	import ModelDetailPage from './models/ModelDetailPage.svelte';
	import { buildModelLibrarySections, modelLibrarySectionCounts, MODELS_ATTRIBUTES_SECTION } from './models/modelLibrarySections';
	import { deletableAttributeIds, selectionHasBuiltIn } from './models/attributeBulkActions';
	import { buildAttributeDefinitionPayload, emptyAttributeDraft, type AttributeDraft } from './attributeDefinitionForm';
	import {
		ATTRIBUTES_SORT_OPTIONS,
		applyAttributesFilters,
		attributesFiltersFromSearchParams,
		attributesFiltersToSearchParams,
		type AttributesFilters,
		type AttributesSortBy
	} from './attributesFilters';
	import AttributeDefinitionForm from './AttributeDefinitionForm.svelte';
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

	type ModelsView = 'table' | 'grid';

	let models = $state<ModelListItem[]>([]);
	let modelTypes = $state<ModelTypeInfo[]>([]);
	let loading = $state(true);
	let availableTags = $state<ModelTag[]>([]);
	let tagSearchQuery = $state('');
	let isTagDropdownOpen = $state(false);
	let unindexedCount = $state<UnindexedModelsCount | null>(null);
	let totalCount = $state(0);
	let availabilityIndexed = $state(false);
	let backendNames = $state<Record<string, string>>({});
	let assignmentSummary = $state<AssignmentSummary>({});
	let selectedModelIds = $state<Set<string>>(new Set());
	let listView = $state<ModelsView>(readListView());
	let currentPage = $state(1);
	let pageSize = $state(30);

	let assigningModel = $state<ModelListItem | null>(null);
	let isAssignModalOpen = $state(false);
	let bulkModelIds = $state<string[]>([]);
	let bulkAdapter = $state<AssignmentAdapter | null>(null);
	let isBulkAssignOpen = $state(false);
	let bulkProgress = $state({ done: 0, total: 0 });
	const bulkBusy = $derived(bulkProgress.total > 0 && bulkProgress.done < bulkProgress.total);

	let tagDropdownRef: HTMLElement | undefined = $state();

	let definitions = $state<AttributeDefinition[]>([]);
	let attrLoading = $state(true);
	let attrError = $state<string | null>(null);
	let modelTypeOptions = $state<string[]>([]);
	let selectedAttributeIds = $state<Set<string>>(new Set());
	let showCreateAttrModal = $state(false);
	let createAttrDraft = $state<AttributeDraft>(emptyAttributeDraft());
	let creatingAttr = $state(false);
	let deleteAttrTarget = $state<AttributeDefinition | null>(null);
	let deletingAttr = $state(false);
	let bulkDeletingAttrs = $state(false);

	function readListView(): ModelsView {
		try {
			return localStorage.getItem('admin-models-view:list') === 'grid' ? 'grid' : 'table';
		} catch {
			return 'table';
		}
	}

	function setListView(next: ModelsView) {
		listView = next;
		try {
			localStorage.setItem('admin-models-view:list', next);
		} catch {
			return;
		}
	}

	const section = $derived($page.url.searchParams.get('view') || 'all');
	const viewId = $derived($page.url.searchParams.get('id'));
	const detailOpen = $derived(!!viewId);
	const isAttributesSection = $derived(section === MODELS_ATTRIBUTES_SECTION);

	const sections = $derived(buildModelLibrarySections(modelTypes));
	const sectionCounts = $derived(modelLibrarySectionCounts(modelTypes, definitions.length));

	const filters = $derived(modelsFiltersFromSearchParams($page.url.searchParams));
	const attrFilters = $derived(attributesFiltersFromSearchParams($page.url.searchParams));

	const filteredAttributeDefinitions = $derived(applyAttributesFilters(definitions, attrFilters));
	const attrIsFiltered = $derived(!!attrFilters.q.trim());

	const activeFilterCount = $derived(modelsFilterActiveCount(filters));
	const filterChips = $derived(modelsFilterChips(filters, availableTags));
	const hasActiveFilters = $derived(modelsHasActiveFilters(filters));

	const filteredTags = $derived(
		availableTags.filter((tag) => tag.name.toLowerCase().includes(tagSearchQuery.toLowerCase()))
	);

	const selectedModel = $derived(viewId && !isAttributesSection ? (models.find((m) => m.id === viewId) ?? null) : null);
	const selectedDefinition = $derived(
		viewId && isAttributesSection ? (definitions.find((d) => d.id === viewId) ?? null) : null
	);

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;

	function buildUrl(
		overrides: { section?: string; id?: string | null; modelFilters?: ModelsFilters; attrFilters?: AttributesFilters } = {}
	): string {
		const nextSection = overrides.section ?? section;
		const params = new URLSearchParams();
		params.set('tab', 'models');
		if (nextSection !== 'all') params.set('view', nextSection);
		if (nextSection === MODELS_ATTRIBUTES_SECTION) {
			for (const [key, value] of attributesFiltersToSearchParams(overrides.attrFilters ?? attrFilters)) params.set(key, value);
		} else {
			for (const [key, value] of modelsFiltersToSearchParams(overrides.modelFilters ?? filters)) params.set(key, value);
		}
		const id = overrides.id !== undefined ? overrides.id : viewId;
		if (id) params.set('id', id);
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	function updateFilters(next: ModelsFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ modelFilters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function updateAttrFilters(next: AttributesFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ attrFilters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function selectSection(id: string) {
		selectedModelIds = new Set();
		selectedAttributeIds = new Set();
		void goto(buildUrl({ section: id, id: null }));
	}

	function openModelId(id: string) {
		void goto(buildUrl({ id }));
	}

	function openAttributeId(id: string) {
		void goto(buildUrl({ id }));
	}

	function backToList() {
		void goto(buildUrl({ id: null }));
	}

	onMount(() => {
		const handleClickOutside = (event: MouseEvent) => {
			if (tagDropdownRef && !tagDropdownRef.contains(event.target as Node)) {
				isTagDropdownOpen = false;
			}
		};

		Promise.all([loadModels(), loadModelTypes(), loadAvailableTags(), loadBackendNames(), loadAssignmentSummary(), loadUnindexedCount()])
			.catch((error) => logger.error('Error loading models data:', error))
			.finally(() => {
				loading = false;
			});
		Promise.all([loadDefinitions(), loadModelTypesDictionaryForAttributes()]).finally(() => {
			attrLoading = false;
		});

		document.addEventListener('mousedown', handleClickOutside);
		return () => document.removeEventListener('mousedown', handleClickOutside);
	});

	$effect(() => {
		void filters;
		void section;
		currentPage = 1;
		if (!loading && !isAttributesSection) loadModels();
	});

	async function loadModels() {
		try {
			const { sort_by, sort_order } = modelsSortParams(filters.sortBy);
			const response = await api.getModels({
				model_type: section === 'all' || isAttributesSection ? undefined : section,
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

	function changeModelsPage(next: number) {
		currentPage = next;
		void loadModels();
	}

	function changeModelsPageSize(next: number) {
		pageSize = next;
		currentPage = 1;
		void loadModels();
	}

	async function loadAssignmentSummary() {
		try {
			const response = await getModelAssignmentSummary();
			if (response.success && response.data) assignmentSummary = response.data;
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
			if (response.success) modelTypes = response.data?.types || [];
		} catch (error) {
			logger.error('Error loading model types:', error);
		}
	}

	async function loadAvailableTags() {
		try {
			const response = await api.getTags('MODEL');
			if (response.success) availableTags = response.data?.tags || [];
		} catch (error) {
			logger.error('Error loading available tags:', error);
		}
	}

	async function loadUnindexedCount() {
		try {
			const response = await api.getUnindexedModelsCount();
			if (response.success && response.data) unindexedCount = response.data;
		} catch (error) {
			logger.error('Error loading unindexed models count:', error);
		}
	}

	async function handleCleanup() {
		if (
			await confirmDialog({
				title: 'Remove models from index',
				message: 'Remove models from index that no longer exist on disk?',
				variant: 'danger'
			})
		) {
			try {
				const response = await api.cleanupDeletedModels();
				if (response.success) {
					await Promise.all([loadModels(), loadModelTypes(), loadUnindexedCount()]);
				}
			} catch (error) {
				logger.error('Error cleaning up models:', error);
			}
		}
	}

	async function handleDeleteModel(modelId: string, name: string) {
		if (
			await confirmDialog({
				title: `Are you sure you want to remove "${name}" from the index?`,
				message: 'This will not delete the file.',
				variant: 'danger'
			})
		) {
			try {
				const response = await api.deleteModel(modelId);
				if (response.success) await Promise.all([loadModels(), loadModelTypes(), loadUnindexedCount()]);
			} catch (error) {
				logger.error('Error deleting model:', error);
			}
		}
	}

	function handleModelDeleted() {
		selectedModelIds = new Set();
		backToList();
		void Promise.all([loadModels(), loadModelTypes(), loadUnindexedCount()]);
	}

	function handleModelAssignChanged(modelId: string, change: { userCount: number; groupCount: number }) {
		assignmentSummary = applyAssignmentSummaryChange(assignmentSummary, modelId, change);
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
		selectedModelIds = new Set();
		loadAssignmentSummary();
	}

	function handleToggleTag(tagId: string) {
		const next = filters.tags.includes(tagId) ? filters.tags.filter((id) => id !== tagId) : [...filters.tags, tagId];
		updateFilters({ ...filters, tags: next });
	}

	function handleRemoveTag(tagId: string) {
		updateFilters({ ...filters, tags: filters.tags.filter((id) => id !== tagId) });
	}

	let deletingTagId = $state<string | null>(null);

	async function handleDeleteTag(tag: ModelTag, event: MouseEvent) {
		event.stopPropagation();
		if (
			!(await confirmDialog({
				title: `Delete tag "${tag.name}"?`,
				message: 'This cannot be undone.',
				variant: 'danger'
			}))
		)
			return;
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

	async function loadDefinitions() {
		attrError = null;
		try {
			const response = await api.getAttributeDefinitions();
			if (response.success && response.data) {
				definitions = response.data.definitions;
			} else {
				attrError = response.error || response.message || 'Failed to load attributes';
			}
		} catch (e: unknown) {
			attrError = getApiErrorMessage(e, 'Failed to load attributes');
		}
	}

	async function loadModelTypesDictionaryForAttributes() {
		try {
			const response = await api.getModelTypes({ include_empty: true });
			if (response.success && response.data) modelTypeOptions = response.data.types.map((t) => t.type).sort();
		} catch (e: unknown) {
			logger.warn('Failed to load model types:', e);
		}
	}

	function openCreateAttrModal() {
		createAttrDraft = emptyAttributeDraft();
		showCreateAttrModal = true;
	}

	async function saveCreateAttr() {
		creatingAttr = true;
		try {
			const payload = buildAttributeDefinitionPayload(createAttrDraft);
			const response = await api.createAttributeDefinition(payload);
			if (response.success) {
				await loadDefinitions();
				showCreateAttrModal = false;
				const created = response.data?.definition;
				toasts.success(`Attribute "${payload.label}" created`);
				if (created?.id) openAttributeId(created.id);
			} else {
				toasts.error(response.error || response.message || 'Failed to create attribute');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to create attribute'));
		} finally {
			creatingAttr = false;
		}
	}

	async function saveAttributeEdit(definition: AttributeDefinition, draft: AttributeDraft): Promise<boolean> {
		try {
			const payload = buildAttributeDefinitionPayload(draft);
			const response = await api.updateAttributeDefinition(definition.id, payload);
			if (response.success) {
				toasts.success(`"${payload.label}" updated`);
				await loadDefinitions();
				return true;
			}
			toasts.error(response.error || response.message || 'Failed to save attribute');
			return false;
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to save attribute'));
			return false;
		}
	}

	async function deleteOneAttribute(definition: AttributeDefinition) {
		deletingAttr = true;
		try {
			const response = await api.deleteAttributeDefinition(definition.id);
			if (response.success) {
				const wasSelected = viewId === definition.id;
				await loadDefinitions();
				deleteAttrTarget = null;
				if (wasSelected) backToList();
			} else {
				toasts.error(response.error || response.message || 'Failed to delete attribute');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to delete attribute'));
		} finally {
			deletingAttr = false;
		}
	}

	async function bulkDeleteAttributes() {
		const ids = deletableAttributeIds(definitions, selectedAttributeIds);
		if (!ids.length) return;
		bulkDeletingAttrs = true;
		try {
			await Promise.all(ids.map((id) => api.deleteAttributeDefinition(id)));
			await loadDefinitions();
			selectedAttributeIds = new Set();
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to delete the selected attributes'));
		} finally {
			bulkDeletingAttrs = false;
		}
	}

	function firstThumbnailIcon(model: ModelListItem): string {
		return typeof model.model_type === 'string' && model.model_type === 'lora' ? 'sparkles' : 'model';
	}

	function baseLabel(model: ModelListItem): string {
		const metadata = model.model_metadata as Record<string, unknown> | undefined;
		const base = metadata?.base_model;
		return typeof base === 'string' && base ? base : '—';
	}

	function assignedCountFor(modelId: string): number {
		const entry = assignmentSummary[modelId];
		return (entry?.assignment_count || 0) + (entry?.group_count || 0);
	}

	const columns: DataTableColumn<ModelListItem>[] = $derived.by(() => [
		{ key: 'name', label: 'Name', width: 'minmax(200px,2fr)', cell: nameCell },
		{ key: 'type', label: 'Type', width: '110px', cell: typeCell },
		{ key: 'base', label: 'Base', width: '110px', priority: 1, accessor: baseLabel },
		{ key: 'size', label: 'Size', width: '90px', mono: true, priority: 1, accessor: (m) => (m.file_size ? formatBytes(m.file_size as number) : '—') },
		{ key: 'availability', label: 'Availability', width: '140px', priority: 1, cell: availabilityCell },
		{ key: 'tags', label: 'Tags', width: 'minmax(120px,1.2fr)', priority: 2, cell: tagsCell },
		{ key: 'assigned', label: 'Assigned', width: '90px', mono: true, priority: 2, accessor: (m) => assignedCountFor(m.id) }
	]);
</script>

{#snippet nameCell(model: ModelListItem)}
	<div class="flex items-center gap-2 min-w-0">
		<span class="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded bg-surface-3 text-fg-subtle">
			<Icon name={firstThumbnailIcon(model)} className="w-3.5 h-3.5" />
		</span>
		<Tooltip text={modelDisplayName(model)}>
			<span class="truncate">{modelDisplayName(model)}</span>
		</Tooltip>
		{#if isModelUnassigned(assignmentSummary[model.id])}
			<Tooltip text="Only admins can see this — assign users or groups">
				<Badge variant="warning" size="sm">Unassigned</Badge>
			</Tooltip>
		{/if}
	</div>
{/snippet}

{#snippet typeCell(model: ModelListItem)}
	<Badge variant="neutral" size="sm" class="uppercase">{model.model_type as string}</Badge>
{/snippet}

{#snippet availabilityCell(model: ModelListItem)}
	{@const backendIds = (model.backend_ids as string[] | undefined) || []}
	{@const known = isAvailabilityKnown(backendIds, availabilityIndexed)}
	{#if !known}
		<StatusCell tone="muted" label="Unknown" />
	{:else if backendIds.length > 0}
		<StatusCell tone="success" label="{backendIds.length} backend{backendIds.length === 1 ? '' : 's'}" />
	{:else}
		<StatusCell tone="danger" label="No backend" />
	{/if}
{/snippet}

{#snippet tagsCell(model: ModelListItem)}
	{@const tags = (model.tags as { id: string; name: string }[] | undefined) || []}
	{#if tags.length === 0}
		<span class="text-fg-subtle">—</span>
	{:else}
		<div class="flex items-center gap-1 min-w-0">
			<Badge variant="neutral" size="sm">{tags[0].name}</Badge>
			{#if tags.length > 1}<Badge variant="neutral" size="sm">+{tags.length - 1}</Badge>{/if}
		</div>
	{/if}
{/snippet}

{#snippet modelCard(model: ModelListItem)}
	<div class="truncate text-sm font-semibold text-fg">{modelDisplayName(model)}</div>
	<div class="mt-1 flex items-center gap-2">
		<Badge variant="neutral" size="sm" class="uppercase">{model.model_type as string}</Badge>
		<span class="font-mono text-2xs text-fg-subtle">{model.file_size ? formatBytes(model.file_size as number) : ''}</span>
	</div>
{/snippet}

<LibraryShell
	title="Models"
	persistKey="admin-models-library"
	heightClass="h-full"
	{sections}
	{section}
	onSelectSection={selectSection}
	{sectionCounts}
	count={isAttributesSection ? filteredAttributeDefinitions.length : models.length}
	{detailOpen}
	filterChips={isAttributesSection ? [] : filterChips}
	onRemoveChip={(key) => updateFilters(clearModelsFilterChip(filters, key))}
	onClearFilters={() => updateFilters(clearAllModelsFilters(filters))}
	loadedCount={isAttributesSection ? filteredAttributeDefinitions.length : models.length}
	total={isAttributesSection ? definitions.length : totalCount}
>
	{#snippet toolbar()}
		{#if isAttributesSection}
			<LibraryFilterBar
				q={attrFilters.q}
				onQueryChange={(value) => updateAttrFilters({ ...attrFilters, q: value })}
				searchPlaceholder="Search by key or label…"
				sortBy={attrFilters.sortBy}
				sortOptions={ATTRIBUTES_SORT_OPTIONS}
				onSortChange={(value) => updateAttrFilters({ ...attrFilters, sortBy: value as AttributesSortBy })}
			/>
		{:else}
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
					<FilterPopoverFrame label="Model filters" onClearAll={() => updateFilters(clearAllModelsFilters(filters))} onClose={close}>
						<div class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-medium text-fg-muted">Tags</span>
							<div bind:this={tagDropdownRef} class="relative">
								<input
									type="text"
									placeholder="Filter by tags…"
									bind:value={tagSearchQuery}
									onfocus={() => (isTagDropdownOpen = true)}
									class="input"
								/>
								{#if isTagDropdownOpen}
									<div class="absolute top-full left-0 right-0 z-50 mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-48 overflow-auto">
										{#if filteredTags.length === 0}
											<div class="p-2 text-sm text-fg-subtle text-center">No tags found</div>
										{:else}
											{#each filteredTags as tag (tag.id)}
												<div class="w-full flex items-center gap-1 pr-1 hover:bg-surface-3 {filters.tags.includes(tag.id) ? 'bg-surface-2 text-fg' : ''}">
													<button
														type="button"
														class="flex-1 min-w-0 text-left px-3 py-2 text-sm flex items-center justify-between"
														onclick={() => handleToggleTag(tag.id)}
													>
														<span class="truncate">{tag.name}</span>
														<span class="text-fg-subtle ml-2 flex-shrink-0">{tag.model_count ? `(${tag.model_count})` : ''}</span>
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
												<button type="button" onclick={() => handleRemoveTag(tagId)} aria-label={`Remove tag ${tag.name}`}>
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
		{/if}
	{/snippet}

	{#snippet primary()}
		{#if isAttributesSection}
			<Button variant="primary" size="sm" icon="plus" onclick={openCreateAttrModal}>New attribute</Button>
		{:else}
			<div class="flex flex-shrink-0 items-center gap-0.5 rounded bg-surface-2 p-0.5" role="radiogroup" aria-label="Models view">
				<Tooltip text="Table view">
					<button
						type="button"
						role="radio"
						aria-checked={listView === 'table'}
						class="rounded px-2 py-1 text-xs font-medium transition-colors {listView === 'table' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3 hover:text-fg'}"
						onclick={() => setListView('table')}
					>
						<Icon name="list" className="w-3.5 h-3.5" />
					</button>
				</Tooltip>
				<Tooltip text="Card view">
					<button
						type="button"
						role="radio"
						aria-checked={listView === 'grid'}
						class="rounded px-2 py-1 text-xs font-medium transition-colors {listView === 'grid' ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3 hover:text-fg'}"
						onclick={() => setListView('grid')}
					>
						<Icon name="grid" className="w-3.5 h-3.5" />
					</button>
				</Tooltip>
			</div>
		{/if}
	{/snippet}

	{#snippet overflow(close)}
		{#if !isAttributesSection}
			{#if unindexedCount && unindexedCount.total > 0}
				<a
					href="/admin?tab=backends"
					role="menuitem"
					class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 hover:bg-surface-2"
					onclick={close}
				>
					<Icon name="server" className="w-3.5 h-3.5" />
					{unindexedCount.total} not indexed
				</a>
			{/if}
			<button
				type="button"
				role="menuitem"
				class="w-full px-3 py-2 text-left text-xs flex items-center gap-2 hover:bg-surface-2"
				onclick={() => {
					close();
					void handleCleanup();
				}}
			>
				<Icon name="trash" className="w-3.5 h-3.5" />
				Cleanup
			</button>
		{/if}
	{/snippet}

	{#if detailOpen}
		{#if isAttributesSection}
			{#if !selectedDefinition}
				<div class="flex h-full items-center justify-center">
					{#if attrLoading}
						<Spinner size="lg" />
					{:else}
						<EmptyState title="Attribute not found" description="This attribute may have been removed." icon="sliders" compact>
							{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToList}>Back to attributes</Button>{/snippet}
						</EmptyState>
					{/if}
				</div>
			{:else}
				<AttributeDetailPage
					definition={selectedDefinition}
					{modelTypeOptions}
					onBack={backToList}
					onDelete={(d) => (deleteAttrTarget = d)}
					onSave={saveAttributeEdit}
				/>
			{/if}
		{:else if !selectedModel}
			<div class="flex h-full items-center justify-center">
				{#if loading}
					<Spinner size="lg" />
				{:else}
					<EmptyState title="Model not found" description="This model may have been removed from the index." icon="model" compact>
						{#snippet actions()}<Button variant="ghost" size="sm" onclick={backToList}>Back to models</Button>{/snippet}
					</EmptyState>
				{/if}
			</div>
		{:else}
			<ModelDetailPage
				modelId={selectedModel.id}
				onBack={backToList}
				onDeleted={handleModelDeleted}
				onAssignChanged={(change) => handleModelAssignChanged(selectedModel.id, change)}
			/>
		{/if}
	{:else if isAttributesSection}
		<div class="flex flex-col gap-3 p-4">
			<SelectionActionBar
				active={selectedAttributeIds.size > 0}
				selectedCount={selectedAttributeIds.size}
				totalCount={filteredAttributeDefinitions.length}
				onSelectAll={() => (selectedAttributeIds = selectPage(selectedAttributeIds, filteredAttributeDefinitions.map((d) => d.id)))}
				onClearSelection={() => (selectedAttributeIds = clearAll())}
				onClose={() => (selectedAttributeIds = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					<Tooltip text={selectionHasBuiltIn(definitions, selectedAttributeIds) ? 'Built-in attributes in this selection can’t be deleted' : 'Delete'}>
						<button
							class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium disabled:opacity-50"
							disabled={selectionHasBuiltIn(definitions, selectedAttributeIds) || deletableAttributeIds(definitions, selectedAttributeIds).length === 0}
							onclick={bulkDeleteAttributes}
						>
							<Icon name="trash" className="w-4 h-4" />
							{bulkDeletingAttrs ? 'Deleting…' : 'Delete'}
						</button>
					</Tooltip>
				</svelte:fragment>
			</SelectionActionBar>
			<AttributesList
				definitions={filteredAttributeDefinitions}
				loading={attrLoading}
				error={attrError}
				isFiltered={attrIsFiltered}
				bind:selected={selectedAttributeIds}
				onOpen={openAttributeId}
				onRetry={loadDefinitions}
				onClearFilters={() => updateAttrFilters({ q: '', sortBy: attrFilters.sortBy })}
			/>
		</div>
	{:else}
		<div class="flex flex-col gap-3 p-4">
			<SelectionActionBar
				active={selectedModelIds.size > 0}
				selectedCount={selectedModelIds.size}
				totalCount={models.length}
				onSelectAll={() => (selectedModelIds = selectPage(selectedModelIds, models.map((m) => m.id)))}
				onClearSelection={() => (selectedModelIds = clearAll())}
				onClose={() => (selectedModelIds = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
						onclick={openBulkAssignModal}
					>
						<Icon name="group" className="w-4 h-4" />
						Assign access
					</button>
				</svelte:fragment>
			</SelectionActionBar>

			{#if listView === 'table'}
				<DataTable
					{columns}
					rows={models}
					getRowId={(m) => m.id}
					onRowClick={(m) => openModelId(m.id)}
					selected={selectedModelIds}
					onSelectedChange={(next) => (selectedModelIds = next)}
					loading={loading && models.length === 0}
					isFiltered={hasActiveFilters}
					card={modelCard}
				>
					{#snippet emptyState()}
						<EmptyState
							icon="model"
							title="No models indexed yet"
							description="Index models from the backend that loads them. They will appear here once found."
						>
							{#snippet actions()}<Button variant="primary" icon="server" href="/admin?tab=backends">Open Backends</Button>{/snippet}
						</EmptyState>
					{/snippet}
					{#snippet filteredEmptyState()}
						<EmptyState title="No models match your filters" description="Try adjusting your search criteria or filters." icon="search" compact>
							{#snippet actions()}<Button variant="ghost" size="sm" onclick={() => updateFilters(DEFAULT_MODELS_FILTERS)}>Clear all filters</Button>{/snippet}
						</EmptyState>
					{/snippet}
				</DataTable>
			{:else if loading}
				<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 gap-6">
					{#each Array.from({ length: 12 }) as _}
						<div class="relative rounded-lg overflow-hidden bg-surface-1 shadow-raised border border-line-strong">
							<div class="aspect-[4/5] w-full bg-surface-2 animate-pulse"></div>
							<div class="p-3 space-y-2">
								<div class="h-3 bg-surface-3 rounded animate-pulse"></div>
								<div class="h-2 bg-surface-2 rounded animate-pulse w-3/4"></div>
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
							selectable={selectedModelIds.size > 0}
							showCheckbox={true}
							selected={selectedModelIds.has(model.id)}
							onSelect={(m) => {
								const next = new Set(selectedModelIds);
								if (next.has(m.id)) next.delete(m.id);
								else next.add(m.id);
								selectedModelIds = next;
							}}
							unassigned={isModelUnassigned(assignmentSummary[model.id])}
							on:view={(e) => openModelId(e.detail.id)}
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
							<Button variant="secondary" icon="close" onclick={() => updateFilters(DEFAULT_MODELS_FILTERS)}>Clear All Filters</Button>
						{:else}
							<Button variant="primary" icon="server" href="/admin?tab=backends">Open Backends</Button>
						{/if}
					{/snippet}
				</EmptyState>
			{/if}

			{#if totalCount > pageSize}
				<TablePager
					page={currentPage}
					pageCount={pageCount(totalCount, pageSize)}
					{pageSize}
					onPageChange={changeModelsPage}
					onPageSizeChange={changeModelsPageSize}
				/>
			{/if}
		</div>
	{/if}
</LibraryShell>

{#if isAssignModalOpen && assigningModel}
	<BaseModal isOpen={isAssignModalOpen} title={`Assign access — ${modelDisplayName(assigningModel)}`} size="lg" on:close={closeAssignModal}>
		<div class="p-6">
			{#key assigningModel.id}
				<AssignmentCard
					adapter={createModelAssignmentAdapter(assigningModel.id)}
					resourceKey={assigningModel.id}
					resourceName={modelDisplayName(assigningModel)}
					on:changed={(e) => handleModelAssignChanged(assigningModel!.id, e.detail)}
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

<BaseModal isOpen={showCreateAttrModal} title="New Attribute" sizeClass="md:max-w-2xl md:w-full" on:close={() => (showCreateAttrModal = false)}>
	<div class="px-6 py-4">
		<AttributeDefinitionForm bind:draft={createAttrDraft} layout="plain" idPrefix="create-attr" locked={false} {modelTypeOptions} />
	</div>
	<svelte:fragment slot="footer">
		<div class="px-6 py-4 flex gap-3">
			<Button
				variant="primary"
				class="flex-1"
				loading={creatingAttr}
				disabled={!createAttrDraft.key.trim() || !createAttrDraft.label.trim()}
				onclick={saveCreateAttr}
			>
				{creatingAttr ? 'Creating…' : 'Create Attribute'}
			</Button>
			<Button variant="secondary" onclick={() => (showCreateAttrModal = false)}>Cancel</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<ConfirmModal
	isOpen={!!deleteAttrTarget}
	title="Delete Attribute"
	message={deleteAttrTarget
		? `Are you sure you want to delete the attribute "${deleteAttrTarget.label}"? Any stored values under its key are orphaned, not removed. This action cannot be undone.`
		: ''}
	variant="danger"
	busy={deletingAttr}
	on:confirm={() => deleteAttrTarget && deleteOneAttribute(deleteAttrTarget)}
	on:cancel={() => (deleteAttrTarget = null)}
/>
