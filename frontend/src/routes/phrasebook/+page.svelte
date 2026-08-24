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
	import PhrasebookToolbar from './components/PhrasebookToolbar.svelte';
	import CategoryTreePane from './components/CategoryTreePane.svelte';
	import ValuesListPane from './components/ValuesListPane.svelte';
	import CategoryInfoView from './components/CategoryInfoView.svelte';
	import CategoryEditForm from './components/CategoryEditForm.svelte';
	import ValueEditForm from './components/ValueEditForm.svelte';

	$: current = $phrasebookStore;

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
	<PhrasebookToolbar />

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
</div>
