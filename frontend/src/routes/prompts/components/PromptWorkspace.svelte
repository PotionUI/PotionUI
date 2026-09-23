<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api';
	import type { PromptImporter } from '$lib/services/api/prompts';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { parseComponentRef } from '$lib/plugin-api/componentRef';
	import { logger } from '$lib/utils/logger';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import type { SortOption } from '$lib/components/library/librarySection';
	import { libraryCounts, setLibraryCount } from '../library/libraryCounts';
	import { LIBRARY_SECTIONS, sectionHref, withSection } from '../library/librarySection';
	import PromptsGrid from './PromptsGrid.svelte';
	import PromptsSidebar from './PromptsSidebar.svelte';
	import PromptFiltersPopover from '$lib/prompts/PromptFiltersPopover.svelte';
	import PromptImportModal from './PromptImportModal.svelte';
	import PromptDetailView from './PromptDetailView.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, EmptyState, IconButton, Spinner } from '$lib/components/ui';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import type {
		Prompt,
		PromptUsageHint,
		ReplacePromptInput,
		Segment,
		RichSegment
	} from '$lib/types/segments';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import CreateTagModal from '$lib/components/modals/CreateTagModal.svelte';
	import {
		applySegmentList,
		createBlankEditorSegment,
		hasMeaningfulSegments,
		toEditorSegment,
		toRichSegment
	} from '$lib/utils/richSegments';
	import { mergeVariables } from '$lib/utils/variablesTransfer';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { promptsCollectionsStore } from '$lib/stores/collections';
	import { tabsStore, activeTab } from '$lib/stores/tabs';
	import {
		DUPLICATE_THRESHOLD_PRESETS,
		removePromptsFromDuplicateGroup,
		type DuplicateGroup
	} from '$lib/utils/duplicatePrompts';
	import {
		promptFiltersFromSearchParams,
		promptFiltersToSearchParams,
		promptFilterActiveCount,
		promptFilterChips,
		clearPromptFilterChip,
		clearAllPromptFilters,
		type PromptSortBy
	} from '$lib/prompts/promptFilters';
	import { queryPromptLibrary } from '$lib/prompts/promptLibraryQuery';

	const PAGE_SIZE = 48;
	const NAME_FIELD_ID = 'prompt-detail-name-field';
	const SORT_OPTIONS: readonly SortOption<PromptSortBy>[] = [
		{ value: 'last_used_at', label: 'Last used' },
		{ value: 'created_at', label: 'Created' },
		{ value: 'name', label: 'Name' },
		{ value: 'usage_count', label: 'Most used' }
	];

	$: filters = promptFiltersFromSearchParams($page.url.searchParams);
	$: collectionId = $page.url.searchParams.get('collection') || undefined;
	$: viewId = $page.url.searchParams.get('id');
	$: viewIsNew = $page.url.searchParams.get('new') === '1';
	$: detailOpen = !!viewId || viewIsNew;
	$: filterCount = promptFilterActiveCount(filters);
	$: filterChips = promptFilterChips(filters, modelLabel !== 'Any' ? modelLabel : undefined);

	function buildUrl(
		overrides: {
			id?: string | null;
			isNew?: boolean;
			collection?: string | null;
			filters?: import('$lib/prompts/promptFilters').PromptFilters;
		} = {}
	): string {
		const params = withSection(promptFiltersToSearchParams(overrides.filters ?? filters), 'prompts');
		const collection = overrides.collection !== undefined ? overrides.collection : collectionId;
		if (collection) params.set('collection', collection);
		const id = overrides.id !== undefined ? overrides.id : viewId;
		const isNew = overrides.isNew !== undefined ? overrides.isNew : viewIsNew;
		if (id) params.set('id', id);
		if (isNew) params.set('new', '1');
		const query = params.toString();
		return query ? `${$page.url.pathname}?${query}` : $page.url.pathname;
	}

	let filtersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateFilters(next: import('$lib/prompts/promptFilters').PromptFilters) {
		clearTimeout(filtersDebounce);
		filtersDebounce = setTimeout(() => {
			void goto(buildUrl({ filters: next }), { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function updateQuery(value: string) {
		updateFilters({ ...filters, q: value });
	}

	function updateSort(value: string) {
		updateFilters({ ...filters, sortBy: value as PromptSortBy });
	}

	function removeChip(key: string) {
		updateFilters(clearPromptFilterChip(filters, key));
	}

	function clearFilters() {
		updateFilters(clearAllPromptFilters(filters));
	}

	function openPrompt(prompt: Prompt) {
		void goto(buildUrl({ id: prompt.id, isNew: false }));
	}

	function backToGrid() {
		void goto(buildUrl({ id: null, isNew: false }));
	}

	let prompts: Prompt[] = [];
	let total = 0;
	let loading = false;
	let loadingMore = false;
	let gridRequestId = 0;
	let selectedIds = new Set<string>();
	let semanticHitCount = 0;

	$: hasMore = prompts.length < total;
	$: searchHint = semanticHitCount > 0 ? `semantic · ${semanticHitCount} hits` : null;

	async function loadGrid(reset: boolean) {
		const requestId = ++gridRequestId;
		if (reset) {
			loading = true;
			selectedIds = new Set();
		} else {
			loadingMore = true;
		}
		try {
			const offset = reset ? 0 : prompts.length;
			const query = filters.q.trim();
			const result = await queryPromptLibrary(api, filters, { collectionId, limit: PAGE_SIZE, offset });
			if (requestId !== gridRequestId) return;
			prompts = reset ? result.rows : [...prompts, ...result.rows];
			total = result.total;
			semanticHitCount = reset ? result.semanticHits : semanticHitCount;
			if (!collectionId && !query && filterCount === 0) setLibraryCount('prompts', result.total);
		} catch {
			if (requestId === gridRequestId) toasts.error('Failed to load prompts');
		} finally {
			if (requestId === gridRequestId) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	function loadMore() {
		void loadGrid(false);
	}

	function toggleSelect(prompt: Prompt) {
		const next = new Set(selectedIds);
		if (next.has(prompt.id)) next.delete(prompt.id);
		else next.add(prompt.id);
		selectedIds = next;
	}

	function selectAll() {
		selectedIds = new Set(prompts.map((p) => p.id));
	}

	function clearSelection() {
		selectedIds = new Set();
	}

	let models: Array<{ id: string; filename?: string; name?: string; model_type?: string; tags?: unknown[] }> = [];
	let showModelFilterPicker = false;

	$: modelLabel = filters.modelId
		? modelDisplayName(models.find((m) => m.id === filters.modelId)) || 'Selected model'
		: 'Any';

	async function loadModels() {
		try {
			const response = await api.getModels({ limit: 200, sort_by: 'filename', sort_order: 'asc' });
			models = response.data?.models || [];
		} catch {
			models = [];
		}
	}

	async function selectModelForFilter(model: { id: string } | null) {
		showModelFilterPicker = false;
		if (model && !models.some((m) => m.id === model.id)) models = [model as never, ...models];
		updateFilters({ ...filters, modelId: model?.id || '' });
	}

	let lastRouteKey = '';
	$: {
		const routeKey = viewId ? `edit:${viewId}` : viewIsNew ? 'new' : 'grid';
		if (routeKey !== lastRouteKey) {
			lastRouteKey = routeKey;
			if (viewId) void enterEdit(viewId);
			else if (viewIsNew) enterCreate();
		}
	}

	let lastGridKey = '';
	$: if (!viewId && !viewIsNew) {
		const gridKey = JSON.stringify({ filters, collectionId });
		if (gridKey !== lastGridKey) {
			lastGridKey = gridKey;
			void loadGrid(true);
		}
	}

	loadModels();
	promptsCollectionsStore.load();
	$: promptCollections = $promptsCollectionsStore.collections;

	let mode: 'create' | 'edit' = 'edit';
	let selected: Prompt | null = null;
	let name = '';
	let usageHint: PromptUsageHint | '' = '';
	let editModelId: string | null = null;
	let editModelLabel: string | null = null;
	let editorSegments: Segment[] = [createBlankEditorSegment()];
	let editorVariables: VariablesMap = {};
	let saving = false;
	let variablesModalOpen = false;
	let dirtySnapshot = '';

	const USAGE_STRIP_LIMIT = 12;
	let usageItems: GenerationHistoryItem[] = [];
	let usageTotal = 0;
	let usageLoading = false;
	let usageRequestId = 0;

	function handleVariableDefChange(variableName: string, def: VariableDef) {
		editorVariables = { ...editorVariables, [variableName]: def };
	}

	function snapshotOf() {
		return JSON.stringify({
			name: name.trim(),
			usageHint,
			editModelId,
			segments: editorSegments.map(toRichSegment),
			variables: editorVariables
		});
	}

	function computeDirtyCount(
		_name: string,
		_usageHint: string,
		_editModelId: string | null,
		_editorSegments: Segment[],
		_editorVariables: VariablesMap
	): number {
		if (!dirtySnapshot) return 0;
		try {
			const before = JSON.parse(dirtySnapshot);
			const after = JSON.parse(snapshotOf());
			let count = 0;
			if (before.name !== after.name) count++;
			if (before.usageHint !== after.usageHint) count++;
			if (before.editModelId !== after.editModelId) count++;
			if (JSON.stringify(before.segments) !== JSON.stringify(after.segments)) count++;
			if (JSON.stringify(before.variables) !== JSON.stringify(after.variables)) count++;
			return count;
		} catch {
			return 0;
		}
	}
	$: dirtyCount = computeDirtyCount(name, usageHint, editModelId, editorSegments, editorVariables);

	function resetEditFields() {
		selected = null;
		name = '';
		usageHint = '';
		editModelId = collectionId ? editModelId : null;
		editModelLabel = null;
		editorSegments = [createBlankEditorSegment()];
		editorVariables = {};
		usageItems = [];
		usageTotal = 0;
	}

	function enterCreate() {
		mode = 'create';
		resetEditFields();
		dirtySnapshot = snapshotOf();
		tick().then(() => document.getElementById(NAME_FIELD_ID)?.focus());
	}

	async function enterEdit(id: string) {
		mode = 'edit';
		let target: Prompt | null = prompts.find((p) => p.id === id) || (selected?.id === id ? selected : null);
		if (!target) {
			try {
				const response = await api.getPrompt(id);
				target = response.success ? response.data ?? null : null;
			} catch {
				target = null;
			}
		}
		if (!target) {
			toasts.error('Prompt not found');
			backToGrid();
			return;
		}
		selected = target;
		name = target.name || '';
		usageHint = target.usage_hint || '';
		editModelId = target.model_id ?? null;
		editModelLabel = target.model_name ?? null;
		editorSegments = target.segments.map((segment) => toEditorSegment(segment));
		if (!editorSegments.length) editorSegments = [createBlankEditorSegment()];
		editorVariables = target.variables ?? {};
		dirtySnapshot = snapshotOf();
		loadUsage(target.id);
	}

	async function loadUsage(promptId: string) {
		const requestId = ++usageRequestId;
		usageLoading = true;
		usageItems = [];
		usageTotal = 0;
		try {
			const response = await api.getPromptGenerations(promptId, { limit: USAGE_STRIP_LIMIT });
			if (requestId !== usageRequestId) return;
			if (response.success && response.data) {
				usageItems = response.data.items;
				usageTotal = response.data.total;
			}
		} catch {
			if (requestId === usageRequestId) toasts.error('Failed to load usage history');
		} finally {
			if (requestId === usageRequestId) usageLoading = false;
		}
	}

	async function saveEditor() {
		if (mode === 'create') return createDraft();
		if (!selected) return;
		saving = true;
		const payload = {
			name: name.trim() || null,
			usage_hint: usageHint || null,
			model_id: editModelId,
			segments: editorSegments.map(toRichSegment),
			variables: Object.keys(editorVariables).length ? editorVariables : null
		};
		try {
			const response = await api.replacePrompt(selected.id, payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success('Prompt updated');
			selected = response.data;
			dirtySnapshot = snapshotOf();
			prompts = prompts.map((p) => (p.id === selected!.id ? (response.data as Prompt) : p));
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save prompt');
		} finally {
			saving = false;
		}
	}

	async function createDraft() {
		if (!hasMeaningfulSegments(editorSegments)) return;
		saving = true;
		const payload = {
			name: name.trim() || null,
			usage_hint: usageHint || null,
			model_id: editModelId,
			segments: editorSegments.map(toRichSegment),
			variables: Object.keys(editorVariables).length ? editorVariables : null
		};
		try {
			const response = await api.createPrompt(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Create failed');
			toasts.success('Prompt created');
			lastGridKey = '';
			await loadGrid(true);
			void goto(buildUrl({ id: response.data.id, isNew: false }));
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to create prompt');
		} finally {
			saving = false;
		}
	}

	async function discardEditor() {
		if (mode === 'create') {
			if (hasMeaningfulSegments(editorSegments)) {
				const ok = await confirmDialog({
					title: 'Discard new prompt?',
					message: 'This prompt has not been saved yet. Discard it?',
					variant: 'danger'
				});
				if (!ok) return;
			}
			backToGrid();
			return;
		}
		if (selected) {
			name = selected.name || '';
			usageHint = selected.usage_hint || '';
			editModelId = selected.model_id ?? null;
			editModelLabel = selected.model_name ?? null;
			editorSegments = selected.segments.map((segment) => toEditorSegment(segment));
			if (!editorSegments.length) editorSegments = [createBlankEditorSegment()];
			editorVariables = selected.variables ?? {};
			dirtySnapshot = snapshotOf();
		}
	}

	async function applyToActiveTab(
		segments: RichSegment[],
		variables: VariablesMap | null | undefined,
		usageHintValue: string,
		sourcePromptId: string | null
	) {
		const tab = get(activeTab);
		if (!tab) {
			toasts.error('No active Generate tab to apply this prompt to');
			return;
		}
		const isNegative = usageHintValue === 'negative';
		const currentList = (isNegative ? tab.negativePromptSegments : tab.promptSegments) ?? [];
		const nextList = applySegmentList(currentList, segments, 'replace');
		const updates: Record<string, unknown> = isNegative
			? { negativePromptSegments: nextList, sourcePromptId }
			: { promptSegments: nextList, sourcePromptId };
		const importedCount = variables ? Object.keys(variables).length : 0;
		if (importedCount > 0)
			updates.variables = mergeVariables(tab.variables ?? {}, variables as VariablesMap, 'replace');
		tabsStore.updateTab(tab.id, updates);
		if (importedCount > 0) toasts.success(`Imported ${importedCount} variable${importedCount === 1 ? '' : 's'}`);
		toasts.success('Prompt applied to the active Generate tab');
		await goto('/');
	}

	function useFromCard(prompt: Prompt) {
		void applyToActiveTab(prompt.segments, prompt.variables, prompt.usage_hint || '', prompt.id);
	}

	function useFromEditor() {
		void applyToActiveTab(editorSegments.map(toRichSegment), editorVariables, usageHint, mode === 'edit' ? selected?.id ?? null : null);
	}

	async function copyFromCard(prompt: Prompt) {
		try {
			await navigator.clipboard.writeText(prompt.flattened_text || '');
			toasts.success('Copied to clipboard');
		} catch {
			toasts.error('Failed to copy prompt');
		}
	}

	async function duplicateFromCard(prompt: Prompt) {
		try {
			const response = await api.createPrompt({
				name: prompt.name ? `${prompt.name} copy` : null,
				usage_hint: prompt.usage_hint ?? null,
				model_id: prompt.model_id ?? null,
				segments: prompt.segments
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Duplicate failed');
			toasts.success('Prompt duplicated');
			lastGridKey = '';
			await loadGrid(true);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to duplicate prompt');
		}
	}

	function duplicateFromEditor() {
		if (selected) void duplicateFromCard(selected);
	}

	function addToCollectionFromCard(prompt: Prompt) {
		selectedIds = new Set([prompt.id]);
		toasts.success('Selected - use "Add to collection…" below');
	}

	async function exportPrompts() {
		try {
			await api.downloadPromptsExport(collectionId ? { collection_id: collectionId } : {});
		} catch {
			toasts.error('Failed to export prompts');
		}
	}

	async function deleteFromCard(prompt: Prompt) {
		const ok = await confirmDialog({
			title: 'Delete',
			message: `Delete "${prompt.display_name}"?`,
			variant: 'danger'
		});
		if (!ok) return;
		try {
			await api.deletePrompt(prompt.id);
			toasts.success('Prompt deleted');
			if (viewId === prompt.id) backToGrid();
			lastGridKey = '';
			await loadGrid(true);
		} catch {
			toasts.error('Failed to delete prompt');
		}
	}

	function deleteFromEditor() {
		if (selected) void deleteFromCard(selected);
	}

	async function bulkAddToCollection(targetCollectionId: string): Promise<boolean> {
		if (selectedIds.size === 0) return false;
		try {
			const response = await api.addPromptsToCollection(targetCollectionId, Array.from(selectedIds), 'prompts');
			if (response.success) {
				await promptsCollectionsStore.load();
				toasts.success('Added to collection');
				clearSelection();
				return true;
			}
			toasts.error('Failed to add to collection');
			return false;
		} catch {
			toasts.error('Failed to add to collection');
			return false;
		}
	}

	async function bulkCreateAndAddToCollection(name: string): Promise<boolean> {
		const created = await promptsCollectionsStore.create(name);
		const collection = created.success ? created.data?.collection : undefined;
		if (!collection) {
			toasts.error('Failed to create collection');
			return false;
		}
		return bulkAddToCollection(collection.id);
	}

	let showBulkTagModal = false;

	function bulkAddTag() {
		if (selectedIds.size === 0) return;
		showBulkTagModal = true;
	}

	async function applyTagToSelection(tagName: string): Promise<{ success: boolean }> {
		const targets = prompts.filter((p) => selectedIds.has(p.id));
		if (targets.length === 0) return { success: false };
		let succeeded = 0;
		for (const target of targets) {
			const nextTags = target.tags?.includes(tagName) ? target.tags : [...(target.tags ?? []), tagName];
			const payload: ReplacePromptInput = {
				name: target.name ?? null,
				usage_hint: target.usage_hint ?? null,
				model_id: target.model_id ?? null,
				segments: target.segments,
				variables: target.variables ?? null,
				tags: nextTags
			};
			try {
				const response = await api.replacePrompt(target.id, payload);
				if (response.success) succeeded++;
			} catch {
				continue;
			}
		}
		if (succeeded > 0) {
			toasts.success(`Tagged ${succeeded} prompt${succeeded === 1 ? '' : 's'}`);
			clearSelection();
			lastGridKey = '';
			await loadGrid(true);
		}
		if (succeeded < targets.length) {
			const failed = targets.length - succeeded;
			toasts.error(`Failed to tag ${failed} prompt${failed === 1 ? '' : 's'}`);
		}
		return { success: succeeded > 0 };
	}

	async function bulkExport() {
		await exportPrompts();
	}

	async function bulkDelete() {
		if (selectedIds.size === 0) return;
		const count = selectedIds.size;
		const ok = await confirmDialog({
			title: `Delete ${count} prompt${count === 1 ? '' : 's'}?`,
			message: "This can't be undone.",
			variant: 'danger'
		});
		if (!ok) return;
		try {
			await api.bulkDeletePrompts(Array.from(selectedIds));
			toasts.success(`Deleted ${count} prompt${count === 1 ? '' : 's'}`);
			if (viewId && selectedIds.has(viewId)) backToGrid();
			clearSelection();
			lastGridKey = '';
			await loadGrid(true);
		} catch {
			toasts.error('Failed to delete prompts');
		}
	}

	async function refreshSelectedMembership(promptId: string) {
		await promptsCollectionsStore.load();
		try {
			const response = await api.getPrompt(promptId);
			if (!response.success || !response.data || selected?.id !== promptId) return;
			selected = { ...selected, collections: response.data.collections ?? [] };
			prompts = prompts.map((p) => (p.id === promptId ? { ...p, collections: response.data!.collections ?? [] } : p));
		} catch {
			return;
		}
	}

	async function addSelectedToCollection(targetCollectionId: string): Promise<boolean> {
		if (!selected) return false;
		const promptId = selected.id;
		try {
			const response = await api.addPromptsToCollection(targetCollectionId, [promptId], 'prompts');
			if (response.success) {
				await refreshSelectedMembership(promptId);
				toasts.success('Added to collection');
				return true;
			}
			toasts.error('Failed to add to collection');
			return false;
		} catch {
			toasts.error('Failed to add to collection');
			return false;
		}
	}

	async function removeSelectedFromCollection(targetCollectionId: string): Promise<boolean> {
		if (!selected) return false;
		const promptId = selected.id;
		try {
			const response = await api.removePromptsFromCollection(targetCollectionId, [promptId], 'prompts');
			if (response.success) {
				await refreshSelectedMembership(promptId);
				toasts.success('Removed from collection');
				return true;
			}
			toasts.error('Failed to remove from collection');
			return false;
		} catch {
			toasts.error('Failed to remove from collection');
			return false;
		}
	}

	async function createAndAddSelectedToCollection(name: string): Promise<boolean> {
		const created = await promptsCollectionsStore.create(name);
		const collection = created.success ? created.data?.collection : undefined;
		if (!collection) {
			toasts.error('Failed to create collection');
			return false;
		}
		return addSelectedToCollection(collection.id);
	}

	export async function startNewPrompt() {
		await goto(buildUrl({ id: null, isNew: true }));
	}

	async function reloadPrompts() {
		lastGridKey = '';
		await loadGrid(true);
	}

	export async function setCollectionFilter(id: string | undefined) {
		await goto(buildUrl({ collection: id ?? null, id: null, isNew: false }));
	}

	let importers: PromptImporter[] = [];
	let activeImporter: PromptImporter | null = null;
	let coreImportOpen = false;
	let exporting = false;

	$: activeImporterRef = activeImporter ? parseComponentRef(activeImporter.component) : null;

	onMount(async () => {
		try {
			const response = await api.listPromptImporters();
			if (response?.success && response.data) importers = response.data;
		} catch (err) {
			logger.error('Failed to load prompt importers:', err);
		}
	});

	function closeImporter() {
		activeImporter = null;
	}

	async function handleImported() {
		activeImporter = null;
		await reloadPrompts();
	}

	function closeCoreImport() {
		coreImportOpen = false;
	}

	async function handleCoreImported() {
		await reloadPrompts();
	}

	async function handleExport() {
		if (exporting) return;
		exporting = true;
		try {
			await exportPrompts();
		} finally {
			exporting = false;
		}
	}

	type DuplicateAction = { kind: 'delete' | 'keep'; groupIndex: number; promptId: string };

	let showDuplicatesModal = false;
	let duplicatesLoading = false;
	let duplicateGroups: DuplicateGroup[] | null = null;
	let duplicateScanPartial = false;
	let duplicateScanScanned = 0;
	let duplicateScanTotal = 0;
	let duplicateThreshold = 0.1;
	let pendingDuplicateAction: DuplicateAction | null = null;
	let duplicateActionBusy = false;

	async function openDuplicatesModal() {
		showDuplicatesModal = true;
		await refreshDuplicates();
	}

	function closeDuplicatesModal() {
		showDuplicatesModal = false;
		duplicateGroups = null;
		pendingDuplicateAction = null;
	}

	async function refreshDuplicates() {
		duplicatesLoading = true;
		duplicateGroups = null;
		duplicateScanPartial = false;
		pendingDuplicateAction = null;
		try {
			const response = await api.findDuplicatePrompts({
				model_id: filters.modelId || undefined,
				threshold: duplicateThreshold
			});
			duplicateGroups = response.data?.groups || [];
			duplicateScanPartial = response.data?.partial ?? false;
			duplicateScanScanned = response.data?.scanned ?? 0;
			duplicateScanTotal = response.data?.total ?? 0;
		} catch {
			toasts.error('Duplicate scan failed');
			duplicateGroups = [];
		} finally {
			duplicatesLoading = false;
		}
	}

	async function selectDuplicateThreshold(value: number) {
		if (duplicateThreshold === value || duplicatesLoading) return;
		duplicateThreshold = value;
		await refreshDuplicates();
	}

	function armDuplicateAction(action: DuplicateAction) {
		pendingDuplicateAction = action;
	}

	function cancelDuplicateAction() {
		pendingDuplicateAction = null;
	}

	async function forgetDeletedSelection(removedIds: string[]) {
		if (viewId && removedIds.includes(viewId)) backToGrid();
		lastGridKey = '';
		await loadGrid(true);
	}

	async function confirmDeleteDuplicate(groupIndex: number, promptId: string) {
		duplicateActionBusy = true;
		try {
			await api.deletePrompt(promptId);
			if (duplicateGroups) duplicateGroups = removePromptsFromDuplicateGroup(duplicateGroups, groupIndex, [promptId]);
			toasts.success('Prompt deleted');
			await forgetDeletedSelection([promptId]);
		} catch {
			toasts.error('Failed to delete prompt');
		} finally {
			duplicateActionBusy = false;
			pendingDuplicateAction = null;
		}
	}

	async function confirmKeepOnly(groupIndex: number, keepId: string) {
		if (!duplicateGroups) return;
		const removeIds = duplicateGroups[groupIndex].prompts.filter((prompt) => prompt.id !== keepId).map((prompt) => prompt.id);
		if (!removeIds.length) return;
		duplicateActionBusy = true;
		try {
			await api.bulkDeletePrompts(removeIds);
			duplicateGroups = removePromptsFromDuplicateGroup(duplicateGroups, groupIndex, removeIds);
			toasts.success(`Removed ${removeIds.length} duplicate${removeIds.length === 1 ? '' : 's'}`);
			await forgetDeletedSelection(removeIds);
		} catch {
			toasts.error('Failed to remove duplicates');
		} finally {
			duplicateActionBusy = false;
			pendingDuplicateAction = null;
		}
	}
</script>

<LibraryShell
	title="Prompt Library"
	persistKey="prompt-library"
	sections={LIBRARY_SECTIONS}
	section="prompts"
	onSelectSection={(id) => goto(sectionHref(id))}
	sectionCounts={$libraryCounts}
	count={total}
	{detailOpen}
	{filterChips}
	onRemoveChip={removeChip}
	onClearFilters={clearFilters}
	loadedCount={prompts.length}
	{total}
>
	{#snippet toolbar()}
		<LibraryFilterBar
			q={filters.q}
			onQueryChange={updateQuery}
			searchPlaceholder="Search prompts…"
			{searchHint}
			sortBy={filters.sortBy}
			sortOptions={SORT_OPTIONS}
			onSortChange={updateSort}
			{filterCount}
		>
			{#snippet popover(close)}
				<PromptFiltersPopover
					{filters}
					{modelLabel}
					onChange={updateFilters}
					onOpenModelPicker={() => (showModelFilterPicker = true)}
					onClose={close}
				/>
			{/snippet}
		</LibraryFilterBar>
	{/snippet}

	{#snippet sidebarTree()}
		<PromptsSidebar
			activeId={collectionId}
			onSelectAll={() => setCollectionFilter(undefined)}
			onSelectFolder={(id) => setCollectionFilter(id)}
		/>
	{/snippet}

	{#snippet overflow(close)}
		<button
			type="button"
			role="menuitem"
			class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
			onclick={() => {
				close();
				coreImportOpen = true;
			}}
		>
			<Icon name="upload" className="h-3.5 w-3.5" />
			From file or text
		</button>
		{#each importers as importer (importer.id)}
			<button
				type="button"
				role="menuitem"
				class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
				onclick={() => {
					close();
					activeImporter = importer;
				}}
			>
				<Icon name="upload" className="h-3.5 w-3.5" />
				{importer.label}
			</button>
		{/each}
		<div class="my-1 h-px bg-line"></div>
		<button
			type="button"
			role="menuitem"
			class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg disabled:opacity-50"
			disabled={exporting}
			onclick={() => {
				close();
				void handleExport();
			}}
		>
			<Icon name="download" className="h-3.5 w-3.5" />
			Export styles.csv
		</button>
		<button
			type="button"
			role="menuitem"
			class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
			onclick={() => {
				close();
				void openDuplicatesModal();
			}}
		>
			<Icon name="copy" className="h-3.5 w-3.5" />
			Find duplicates
		</button>
	{/snippet}

	{#snippet primary()}
		<Button size="sm" variant="primary" icon="plus" onclick={startNewPrompt}>New prompt</Button>
	{/snippet}

	{#if detailOpen}
		<PromptDetailView
			{mode}
			prompt={selected}
			bind:name
			bind:usageHint
			bind:editModelId
			bind:editModelLabel
			bind:editorSegments
			bind:editorVariables
			{usageItems}
			{usageTotal}
			{usageLoading}
			{saving}
			{dirtyCount}
			{promptCollections}
			onBack={backToGrid}
			onSave={saveEditor}
			onDiscard={discardEditor}
			onDelete={deleteFromEditor}
			onDuplicate={duplicateFromEditor}
			onUse={useFromEditor}
			onAddToCollection={addSelectedToCollection}
			onCreateAndAddToCollection={createAndAddSelectedToCollection}
			onRemoveFromCollection={removeSelectedFromCollection}
			onOpenVariableManager={() => (variablesModalOpen = true)}
			onVariableDefChange={handleVariableDefChange}
		/>
	{:else}
		<PromptsGrid
			{prompts}
			{loading}
			{loadingMore}
			{hasMore}
			{filters}
			{selectedIds}
			collections={promptCollections}
			onFiltersChange={updateFilters}
			onLoadMore={loadMore}
			onOpen={openPrompt}
			onUse={useFromCard}
			onCopy={copyFromCard}
			onDuplicate={duplicateFromCard}
			onAddToCollection={addToCollectionFromCard}
			onExport={exportPrompts}
			onDeleteOne={deleteFromCard}
			onToggleSelect={toggleSelect}
			onSelectAll={selectAll}
			onClearSelection={clearSelection}
			onBulkAddToCollection={bulkAddToCollection}
			onBulkCreateAndAddToCollection={bulkCreateAndAddToCollection}
			onBulkAddTag={bulkAddTag}
			onBulkExport={bulkExport}
			onBulkDelete={bulkDelete}
			onNewPrompt={startNewPrompt}
		/>
	{/if}
</LibraryShell>

{#if activeImporterRef}
	{#await resolvePluginComponent(activeImporterRef.pluginId, activeImporterRef.asset) then Component}
		{#if Component}
			<svelte:component this={Component} onClose={closeImporter} onImported={handleImported} />
		{/if}
	{/await}
{/if}

{#if coreImportOpen}
	<PromptImportModal onClose={closeCoreImport} onImported={handleCoreImported} />
{/if}

<VariableManagerModal
	isOpen={variablesModalOpen}
	variables={editorVariables}
	on:close={() => (variablesModalOpen = false)}
	on:change={(e) => (editorVariables = e.detail)}
/>

{#if showModelFilterPicker}
	<ModelAssignmentModal
		selectionMode="single"
		selectedModelId={filters.modelId || null}
		allowClear={true}
		title="Filter prompts by model"
		subtitle="Search the model catalog or narrow it by type, then select one model."
		onSelect={selectModelForFilter}
		onClear={() => selectModelForFilter(null)}
		onClose={() => (showModelFilterPicker = false)}
	/>
{/if}

{#if showBulkTagModal}
	<CreateTagModal
		onClose={() => (showBulkTagModal = false)}
		onCreate={applyTagToSelection}
		description={`Adding this tag to the ${selectedIds.size} selected prompt${selectedIds.size === 1 ? '' : 's'}.`}
	/>
{/if}

{#if showDuplicatesModal}
	<BaseModal isOpen={true} title="Duplicate prompts" sizeClass="md:max-w-2xl md:w-full" on:close={closeDuplicatesModal}>
		<svelte:fragment slot="headerIcon">
			<Icon name="copy" className="h-5 w-5 flex-shrink-0 text-fg-muted" />
		</svelte:fragment>

		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-3">
			<div class="flex items-center gap-2">
				<span class="text-xs font-medium text-fg-muted">Similarity</span>
				<div class="inline-flex overflow-hidden rounded border border-line-strong">
					{#each DUPLICATE_THRESHOLD_PRESETS as preset (preset.value)}
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-50 {duplicateThreshold ===
							preset.value
								? 'bg-signal/10 text-signal'
								: 'bg-surface-1 text-fg-muted hover:bg-surface-3/50'}"
							disabled={duplicatesLoading}
							onclick={() => selectDuplicateThreshold(preset.value)}
						>
							{preset.label}
						</button>
					{/each}
				</div>
			</div>
			{#if !duplicatesLoading && duplicateGroups}
				<div class="flex items-center gap-2">
					{#if duplicateScanPartial}
						<Badge variant="warning">
							<Icon name="warning" className="h-3 w-3" />
							Partial scan
						</Badge>
					{/if}
					<Badge>
						<span class="font-mono tabular-nums">{duplicateGroups.length}</span>
						group{duplicateGroups.length === 1 ? '' : 's'}
					</Badge>
				</div>
			{/if}
		</div>

		<div class="max-h-[60vh] min-h-[12rem] space-y-3 overflow-y-auto p-6">
			{#if duplicatesLoading || duplicateGroups === null}
				<div class="flex h-32 items-center justify-center">
					<Spinner size="lg" />
				</div>
			{:else if duplicateGroups.length === 0}
				<EmptyState
					icon="check"
					title="No duplicates found"
					description="Every saved prompt at this similarity level is unique. Try Loose if you expect near-matches to show up."
					compact
				/>
			{:else}
				{#each duplicateGroups as group, groupIndex (group.prompts.map((prompt) => prompt.id).join('-'))}
					<div class="rounded-lg border border-line-strong bg-surface-1 p-3 shadow-raised">
						<div class="mb-2 flex items-center justify-between gap-3">
							<div class="flex items-center gap-2">
								<Badge variant="signal">
									<span class="font-mono tabular-nums">{Math.round(group.similarity * 100)}%</span> match
								</Badge>
								<Badge size="sm">{group.prompts.length} prompts</Badge>
							</div>
						</div>
						<div class="divide-y divide-line">
							{#each group.prompts as prompt, promptIndex (prompt.id)}
								<div class="py-2 first:pt-0 last:pb-0">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0 flex-1">
											<div class="flex items-center gap-1.5">
												<p class="truncate text-sm font-medium text-fg">{prompt.display_name}</p>
												{#if promptIndex === 0}
													<Badge size="sm" variant="success">Suggested keep</Badge>
												{/if}
											</div>
											<p class="mt-0.5 line-clamp-2 text-xs text-fg-subtle">
												{prompt.flattened_text || 'Empty composition'}
											</p>
										</div>
										{#if pendingDuplicateAction?.groupIndex === groupIndex && pendingDuplicateAction.promptId === prompt.id}
											{@const action = pendingDuplicateAction}
											<div class="flex flex-shrink-0 items-center gap-1.5">
												<span class="text-xs text-fg-muted">
													{action.kind === 'keep' ? 'Remove the rest?' : 'Remove this prompt?'}
												</span>
												<Button size="xs" variant="ghost" disabled={duplicateActionBusy} onclick={cancelDuplicateAction}>
													Cancel
												</Button>
												<Button
													size="xs"
													variant="danger"
													loading={duplicateActionBusy}
													onclick={() =>
														action.kind === 'keep'
															? confirmKeepOnly(groupIndex, prompt.id)
															: confirmDeleteDuplicate(groupIndex, prompt.id)}
												>
													Remove
												</Button>
											</div>
										{:else}
											<div class="flex flex-shrink-0 items-center gap-1.5">
												<Button
													size="xs"
													variant="ghost"
													disabled={duplicateActionBusy}
													onclick={() => armDuplicateAction({ kind: 'keep', groupIndex, promptId: prompt.id })}
												>
													Keep only this
												</Button>
												<IconButton
													icon="trash"
													label="Delete this prompt"
													disabled={duplicateActionBusy}
													onclick={() => armDuplicateAction({ kind: 'delete', groupIndex, promptId: prompt.id })}
												/>
											</div>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/each}
			{/if}
		</div>

		<svelte:fragment slot="footer">
			<div class="flex justify-end px-6 py-4">
				<Button onclick={closeDuplicatesModal}>Close</Button>
			</div>
		</svelte:fragment>
	</BaseModal>
{/if}
