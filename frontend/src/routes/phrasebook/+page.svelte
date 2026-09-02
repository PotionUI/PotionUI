<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import { page } from '$app/stores';
	import { DetailEmptyState } from '$lib/components/master-detail';
	import {
		phrasebookStore,
		allActiveValueIds
	} from '$lib/stores/phrasebook';
	import { previewGenerationStore } from '$lib/stores/previewGeneration';
	import CategoryTreePane from './components/CategoryTreePane.svelte';
	import ValuesListPane from './components/ValuesListPane.svelte';
	import CategoryInfoView from './components/CategoryInfoView.svelte';
	import CategoryEditForm from './components/CategoryEditForm.svelte';
	import ValueEditForm from './components/ValueEditForm.svelte';
	import PhrasebookHeader from './components/PhrasebookHeader.svelte';
	import PhrasebookSearchView from './components/PhrasebookSearchView.svelte';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type { PhrasebookFindResult } from '$lib/types/api';
	import {
		apiErrorDetail,
		buildFindParams,
		defaultFilters,
		isSearching,
		topLevelCategories,
		type FindFilters
	} from './phrasebookSearch';

	$: current = $phrasebookStore;

	// Find & replace
	let filters: FindFilters = defaultFilters();
	let findResult: PhrasebookFindResult | null = null;
	let searching = false;
	let findError: string | null = null;
	let findSeq = 0;
	let findTimer: ReturnType<typeof setTimeout> | null = null;

	$: searchActive = isSearching(filters.query);
	$: topLevel = topLevelCategories(current.allCategories);

	function cancelPendingFind() {
		if (findTimer) clearTimeout(findTimer);
		findTimer = null;
	}

	async function runFind() {
		cancelPendingFind();
		const seq = ++findSeq;
		if (!isSearching(filters.query)) {
			findResult = null;
			searching = false;
			findError = null;
			return;
		}
		searching = true;
		try {
			const response = await api.findPhrasebook(buildFindParams(filters));
			if (seq !== findSeq) return;
			if (response.success && response.data) {
				findResult = response.data;
				findError = null;
			} else {
				findError = response.message || response.error || 'Search failed';
			}
		} catch (error) {
			if (seq !== findSeq) return;
			const detail = apiErrorDetail(error);
			findError = detail?.message ?? 'Search failed';
			if (!detail) logger.error('Phrasebook find failed:', error);
		} finally {
			if (seq === findSeq) searching = false;
		}
	}

	function updateFilters(patch: Partial<FindFilters>) {
		filters = { ...filters, ...patch };
		if ('query' in patch) {
			cancelPendingFind();
			if (!isSearching(filters.query)) {
				runFind();
				return;
			}
			findTimer = setTimeout(() => {
				findTimer = null;
				runFind();
			}, 250);
			return;
		}
		if (searchActive) runFind();
	}

	function clearQuery() {
		cancelPendingFind();
		findSeq++;
		filters = { ...filters, query: '' };
		findResult = null;
		searching = false;
		findError = null;
	}

	// Panel widths
	let treeWidth = 280;
	let valuesWidth = 300;
	const minTreeWidth = 200;
	const maxTreeWidth = 400;
	const minValuesWidth = 200;
	const maxValuesWidth = 500;

	// Resizing state
	let isResizingTree = false;
	let isResizingValues = false;

	// URL state management
	let initialized = false;

	// Update URL when selection changes (after initialization)
	$: if (browser && initialized && (current.selectedCategoryId !== undefined || current.selectedValueId !== undefined)) {
		updateUrlParams();
	}

	function updateUrlParams() {
		if (!browser) return;

		const params = new URLSearchParams();
		if (current.selectedCategoryId) params.set('category', current.selectedCategoryId);
		if (current.selectedValueId) params.set('value', current.selectedValueId);

		const newUrl = params.toString() ? `/phrasebook?${params.toString()}` : '/phrasebook';

		const url = new URL(newUrl, window.location.origin);
		window.history.replaceState(window.history.state, '', url);
	}

	// Restore selection from URL and expand tree path
	async function restoreSelectionFromUrl() {
		const urlCategoryId = $page.url.searchParams.get('category');
		const urlValueId = $page.url.searchParams.get('value');

		const categoryExists = current.allCategories.some((c) => c.id === urlCategoryId);

		if (urlCategoryId && categoryExists) {
			await phrasebookStore.expandPathToCategory(urlCategoryId);

			phrasebookStore.setSelectedCategoryId(urlCategoryId);
			await phrasebookStore.loadCategoryValues(urlCategoryId);

			if (urlValueId) {
				const values = $phrasebookStore.categoryValues[urlCategoryId] || [];
				const value = values.find((v) => v.id === urlValueId);
				if (value) {
					phrasebookStore.setSelectedValueId(urlValueId);
					phrasebookStore.setValueForm({
						category_id: value.category_id,
						label: value.label,
						value: value.value,
						sort_order: value.sort_order
					});
					phrasebookStore.setEditMode('value');
				}
			}
		}

		initialized = true;
	}

	// Auto-select all active values when category changes
	$: if (current.selectedCategoryId && $allActiveValueIds.length > 0) {
		phrasebookStore.selectValueIds($allActiveValueIds);
	}

	onMount(async () => {
		const savedTreeWidth = storage.get('phrasebook-tree-width');
		const savedValuesWidth = storage.get('phrasebook-values-width');
		if (savedTreeWidth) treeWidth = Math.max(minTreeWidth, Math.min(maxTreeWidth, parseInt(savedTreeWidth)));
		if (savedValuesWidth) valuesWidth = Math.max(minValuesWidth, Math.min(maxValuesWidth, parseInt(savedValuesWidth)));

		previewGenerationStore.connect();

		await phrasebookStore.loadRootCategories();
		await phrasebookStore.loadAllCategories();
		await previewGenerationStore.loadPresets();

		if (browser) {
			await restoreSelectionFromUrl();
		}
	});

	onDestroy(() => {
		cancelPendingFind();
		previewGenerationStore.disconnect();
	});

	// Resize handlers
	function startResizeTree() {
		isResizingTree = true;
		document.addEventListener('mousemove', handleResizeTree);
		document.addEventListener('mouseup', stopResize);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function startResizeValues() {
		isResizingValues = true;
		document.addEventListener('mousemove', handleResizeValues);
		document.addEventListener('mouseup', stopResize);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function handleResizeTree(e: MouseEvent) {
		if (!isResizingTree) return;
		const newWidth = Math.max(minTreeWidth, Math.min(maxTreeWidth, e.clientX));
		treeWidth = newWidth;
		storage.set('phrasebook-tree-width', String(newWidth));
	}

	function handleResizeValues(e: MouseEvent) {
		if (!isResizingValues) return;
		const newWidth = Math.max(minValuesWidth, Math.min(maxValuesWidth, e.clientX - treeWidth - 4));
		valuesWidth = newWidth;
		storage.set('phrasebook-values-width', String(newWidth));
	}

	function stopResize() {
		isResizingTree = false;
		isResizingValues = false;
		document.removeEventListener('mousemove', handleResizeTree);
		document.removeEventListener('mousemove', handleResizeValues);
		document.removeEventListener('mouseup', stopResize);
		document.body.style.cursor = '';
		document.body.style.userSelect = '';
	}
</script>

<div class="h-screen flex flex-col bg-canvas">
	<PhrasebookHeader
		{filters}
		{searching}
		error={findError}
		{topLevel}
		onChange={updateFilters}
		onClear={clearQuery}
	/>

	{#if searchActive}
		<PhrasebookSearchView
			result={findResult}
			loading={searching}
			{filters}
			onClearQuery={clearQuery}
			onRerun={runFind}
		/>
	{:else}
		<!-- Main Content - 3 Panes -->
		<div class="flex-1 min-h-0 flex">
			<CategoryTreePane width={treeWidth} />

			<!-- Tree resize handle -->
			<button
				type="button"
				class="w-1 bg-line-strong/50 hover:bg-line-hover transition-colors cursor-col-resize flex-shrink-0"
				role="separator"
				aria-orientation="vertical"
				tabindex="0"
				aria-label="Resize tree panel"
				on:mousedown={startResizeTree}
				on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); } }}
			></button>

			<ValuesListPane width={valuesWidth} />

			<!-- Values resize handle -->
			<button
				type="button"
				class="w-1 bg-line-strong/50 hover:bg-line-hover transition-colors cursor-col-resize flex-shrink-0"
				role="separator"
				aria-orientation="vertical"
				tabindex="0"
				aria-label="Resize values panel"
				on:mousedown={startResizeValues}
				on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); } }}
			></button>

			<!-- Right Pane: Editor -->
			<div class="flex-1 min-w-0 flex flex-col bg-canvas">
				{#if current.editMode === 'none' && !current.selectedCategoryId}
					<DetailEmptyState message="Select a category to view its values" icon="folder" />
				{:else if current.editMode === 'none' && current.selectedCategoryId && !current.selectedValueId}
					<CategoryInfoView />
				{:else if current.editMode === 'category' || current.editMode === 'new-category'}
					<CategoryEditForm />
				{:else if current.editMode === 'value' || current.editMode === 'new-value'}
					<ValueEditForm />
				{/if}
			</div>
		</div>
	{/if}
</div>
