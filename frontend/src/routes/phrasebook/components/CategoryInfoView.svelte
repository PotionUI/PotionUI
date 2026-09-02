<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { Badge, IconButton, CopyButton, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailTabs, DetailBody, DetailSection, KVGrid, KVItem } from '$lib/components/detail';
	import {
		phrasebookStore,
		selectedCategory,
		selectedCategoryValues,
		type CategoryDetailTab
	} from '$lib/stores/phrasebook';
	import { previewGenerationStore } from '$lib/stores/previewGeneration';
	import PreviewImagesSection from './PreviewImagesSection.svelte';

	// Self-contained: reads/writes phrasebookStore directly. Right-hand detail
	// panel for a selected category (no value selected) - header actions, then
	// an Overview tab (Details + Subcategories) and a Preview images tab (the
	// generation flow, kept out of Overview so it doesn't dominate the panel).
	let current = $derived($phrasebookStore);
	let values = $derived($selectedCategoryValues);
	let category = $derived($selectedCategory);
	let gen = $derived($previewGenerationStore);
	let parentCategory = $derived(
		category?.parent_id ? current.allCategories.find((c) => c.id === category.parent_id) : null
	);

	const detailTabs: { id: CategoryDetailTab; label: string; icon: string }[] = [
		{ id: 'overview', label: 'Overview', icon: 'info' },
		{ id: 'preview-images', label: 'Preview images', icon: 'image' }
	];
	let tabsWithProgress = $derived(
		detailTabs.map((tab) =>
			tab.id === 'preview-images' && gen.isGeneratingPreviews && gen.previewBatchProgress
				? { ...tab, label: `${tab.label} · ${gen.previewBatchProgress.done}/${gen.previewBatchProgress.total}` }
				: tab
		)
	);

	$effect(() => {
		if (category && !category.childrenLoaded && !current.loadingCategories.has(category.id)) {
			phrasebookStore.loadCategoryChildren(category.id);
		}
	});

	function formatDate(value: string | undefined) {
		return value ? value.slice(0, 10) : '—';
	}

	async function handleExport() {
		if (!current.selectedCategoryId) return;
		try {
			const yamlContent = await api.exportPhrasebookCategory(current.selectedCategoryId);
			const blob = new Blob([yamlContent], { type: 'text/yaml' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${category?.name || 'export'}.yaml`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			logger.error('Failed to export:', error);
		}
	}
</script>

{#if category}
	<div class="flex flex-col h-full">
		<DetailHeader title={category.name} icon="folder">
			{#snippet chips()}
				<Badge size="sm" class="font-mono">{values.length} VALUES</Badge>
				<Badge size="sm" class="font-mono">{category.children?.length ?? 0} SUBCATEGORIES</Badge>
				<Badge variant={category.is_active ? 'success' : 'neutral'} size="sm" class="uppercase">
					{category.is_active ? 'Active' : 'Inactive'}
				</Badge>
			{/snippet}
			{#snippet subtitle()}
				<span>{category.path} / updated {formatDate(category.updated_at)}</span>
			{/snippet}
			{#snippet actions()}
				<Tooltip text={category.is_active ? 'Deactivate category' : 'Activate category'}>
					<IconButton
						icon={category.is_active ? 'check' : 'close'}
						label={category.is_active ? 'Deactivate category' : 'Activate category'}
						active={category.is_active}
						onclick={() => phrasebookStore.handleToggleCategoryActive(category.id)}
					/>
				</Tooltip>
				<Tooltip text="Export as YAML">
					<IconButton icon="download" label="Export as YAML" onclick={handleExport} />
				</Tooltip>
				<Tooltip text="Edit category">
					<IconButton icon="edit" label="Edit category" onclick={() => phrasebookStore.handleEditCategory()} />
				</Tooltip>
				<Tooltip text="Delete category">
					<IconButton
						icon="trash"
						label="Delete category"
						class="text-danger hover:bg-danger/10"
						onclick={() => phrasebookStore.handleDeleteCategory()}
					/>
				</Tooltip>
			{/snippet}
		</DetailHeader>

		<DetailTabs
			tabs={tabsWithProgress}
			active={current.categoryDetailTab}
			onSelect={(id) => phrasebookStore.setCategoryDetailTab(id as CategoryDetailTab)}
			ariaLabel="Category details"
		/>

		{#if current.categoryDetailTab === 'overview'}
			<DetailBody fullWidth>
				<div class="space-y-5">
				<DetailSection label="Details">
					<div class="flex flex-col gap-3.5">
						<p class="text-xs text-fg-muted leading-relaxed">
							A category is a named bag of interchangeable phrases; insert it into a prompt as
							<span
								class="inline-flex items-center gap-1 mx-0.5 rounded bg-signal/10 border border-signal/30 px-1.5 py-0.5 align-middle font-mono text-2xs font-semibold text-signal"
							>
								<Icon name="folder" className="w-3 h-3" />#{category.path}
							</span>
							<CopyButton text={'#' + category.path} ariaLabel="Copy phrasebook reference" size="xs" />
							and a value is picked for you &mdash; shuffle, pinned, or per image.
						</p>

						<KVGrid>
							<KVItem label="Path" mono>{category.path}</KVItem>
							<KVItem label="Parent" mono>{parentCategory?.name ?? '—'}</KVItem>
							<KVItem label="Description" full>
								{#if category.description}
									<div class="flex items-baseline gap-2">
										<span>{category.description}</span>
										<button
											type="button"
											class="text-2xs text-fg-subtle hover:text-fg underline decoration-line-strong hover:decoration-fg-muted flex-shrink-0"
											onclick={() => phrasebookStore.handleEditCategory()}
										>
											Edit
										</button>
									</div>
								{:else}
									<div class="flex items-baseline gap-2">
										<span class="italic text-fg-subtle">No description</span>
										<button
											type="button"
											class="text-2xs text-fg-subtle hover:text-fg underline decoration-line-strong hover:decoration-fg-muted flex-shrink-0"
											onclick={() => phrasebookStore.handleEditCategory()}
										>
											Edit
										</button>
									</div>
								{/if}
							</KVItem>
							<KVItem label="Status">
								<Badge variant={category.is_active ? 'success' : 'neutral'} size="sm">
									{category.is_active ? 'Active' : 'Inactive'}
								</Badge>
							</KVItem>
							<KVItem label="Updated" mono>{formatDate(category.updated_at)}</KVItem>
						</KVGrid>
					</div>
				</DetailSection>

				<DetailSection label="Subcategories">
					{#if !category.childrenLoaded && current.loadingCategories.has(category.id)}
						<div class="flex items-center justify-center py-6">
							<Spinner size="sm" />
						</div>
					{:else if category.children && category.children.length > 0}
						<div class="flex flex-col -mx-4 sm:-mx-5">
							{#each category.children as child (child.id)}
								<button
									type="button"
									class="flex items-center gap-2.5 px-4 sm:px-5 py-2.5 text-left hover:bg-surface-2 transition-colors"
									onclick={() => phrasebookStore.handleSelectCategory(child.id)}
								>
									<span
										class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded bg-surface-2 border border-line-strong text-fg-subtle"
									>
										<Icon name="folder" className="w-3.5 h-3.5" />
									</span>
									<span class="min-w-0 flex-1">
										<span class="block text-sm text-fg font-medium truncate">{child.name}</span>
										<span class="block font-mono text-2xs text-fg-subtle truncate">{child.path}</span>
									</span>
									<Icon name="chevron-right" className="w-3.5 h-3.5 text-fg-disabled flex-shrink-0" />
								</button>
							{/each}
						</div>
					{:else}
						<p class="text-xs text-fg-subtle italic">No subcategories</p>
					{/if}

					{#snippet footer()}
						<button
							type="button"
							class="flex items-center gap-1.5 text-xs text-fg-subtle hover:text-fg"
							onclick={() => phrasebookStore.handleNewCategory()}
						>
							<Icon name="plus" className="w-3.5 h-3.5" />
							Add subcategory
						</button>
					{/snippet}
				</DetailSection>
				</div>
			</DetailBody>
		{:else if current.selectedCategoryId}
			<DetailBody fullWidth>
				<PreviewImagesSection categoryId={current.selectedCategoryId} />
			</DetailBody>
		{/if}
	</div>
{/if}
