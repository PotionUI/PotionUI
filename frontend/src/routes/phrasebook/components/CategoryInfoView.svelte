<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { Badge, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { phrasebookStore, selectedCategory, selectedCategoryValues } from '$lib/stores/phrasebook';
	import PreviewGenerationPanel from './PreviewGenerationPanel.svelte';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (category info / stats view).
	$: current = $phrasebookStore;
	$: values = $selectedCategoryValues;

	async function handleExport() {
		if (!current.selectedCategoryId) return;
		try {
			const yamlContent = await api.exportPhrasebookCategory(current.selectedCategoryId);
			const blob = new Blob([yamlContent], { type: 'text/yaml' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${$selectedCategory?.name || 'export'}.yaml`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			logger.error('Failed to export:', error);
		}
	}
</script>

<div class="flex flex-col h-full">
	<div class="px-6 py-4 border-b border-line flex items-center justify-between">
		<div>
			<div class="flex items-center gap-2">
				<h2 class="text-lg font-semibold text-fg">{$selectedCategory?.name}</h2>
				{#if !$selectedCategory?.is_active}
					<Badge size="sm">inactive</Badge>
				{/if}
			</div>
			<p class="text-sm text-fg-muted font-mono">{$selectedCategory?.path}</p>
		</div>
		<div class="flex items-center gap-2">
			<!-- Toggle active button -->
			<button
				type="button"
				class="p-2 rounded-lg transition-colors
					{$selectedCategory?.is_active ? 'text-success hover:bg-success/10' : 'text-fg-subtle hover:bg-surface-3'}"
				title="{$selectedCategory?.is_active ? 'Deactivate' : 'Activate'} category"
				on:click={() => current.selectedCategoryId && phrasebookStore.handleToggleCategoryActive(current.selectedCategoryId)}
			>
				<Icon name={$selectedCategory?.is_active ? 'check' : 'close'} className="w-5 h-5" />
			</button>
			<IconButton icon="download" label="Export as YAML" onclick={handleExport} />
			<IconButton icon="edit" label="Edit category" onclick={() => phrasebookStore.handleEditCategory()} />
			<button
				type="button"
				class="p-2 rounded-lg transition-colors text-fg-muted hover:text-danger hover:bg-danger/10"
				title="Delete category"
				on:click={() => phrasebookStore.handleDeleteCategory()}
			>
				<Icon name="trash" className="w-5 h-5" />
			</button>
		</div>
	</div>
	<div class="flex-1 p-6">
		{#if $selectedCategory?.description}
			<p class="text-sm text-fg-muted">{$selectedCategory.description}</p>
		{:else}
			<p class="text-sm text-fg-subtle italic">No description</p>
		{/if}

		<div class="mt-6 grid grid-cols-2 gap-4 text-sm">
			<div class="p-3 bg-surface-2 rounded-lg">
				<span class="text-fg-subtle">Values</span>
				<p class="text-xl font-semibold text-fg tabular-nums">{values.length}</p>
			</div>
			<div class="p-3 bg-surface-2 rounded-lg">
				<span class="text-fg-subtle">Children</span>
				<p class="text-xl font-semibold text-fg tabular-nums">{$selectedCategory?.children?.length || 0}</p>
			</div>
		</div>

		<!-- Generate Preview Images Section -->
		{#if values.length > 0 && current.selectedCategoryId}
			<PreviewGenerationPanel categoryId={current.selectedCategoryId} />
		{/if}
	</div>
</div>
