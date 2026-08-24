<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { Pane, PaneRow } from '$lib/components/pane';
	import {
		phrasebookStore,
		selectedCategory,
		selectedCategoryValues,
		selectedCount,
		activeCount,
		isAllSelected
	} from '$lib/stores/phrasebook';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (middle values-list pane).
	let { width }: { width: number } = $props();

	let current = $derived($phrasebookStore);
	let values = $derived($selectedCategoryValues);
</script>

<div class="flex-shrink-0 border-r border-line flex flex-col bg-surface-1/50" style="width: {width}px">
	<Pane
		label={$selectedCategory ? $selectedCategory.name : 'Values'}
		loading={current.valuesLoading}
		isEmpty={!current.selectedCategoryId || values.length === 0}
		bodyRole="listbox"
	>
		{#snippet headerActions()}
			{#if current.selectedCategoryId}
				<IconButton
					icon="plus"
					label="Add value"
					size="sm"
					onclick={() => phrasebookStore.handleNewValue()}
				/>
			{/if}
		{/snippet}

		{#snippet subheader()}
			{#if current.selectedCategoryId && $activeCount > 0}
				<div class="px-3 py-2 border-b border-line flex items-center justify-between flex-shrink-0">
					<div class="flex items-center gap-2">
						<button
							type="button"
							class="text-xs text-fg-muted hover:text-fg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
							onclick={() => phrasebookStore.selectAllValues()}
							disabled={$isAllSelected}
						>
							Select All
						</button>
						<span class="text-line-strong">|</span>
						<button
							type="button"
							class="text-xs text-fg-muted hover:text-fg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
							onclick={() => phrasebookStore.deselectAllValues()}
							disabled={$selectedCount === 0}
						>
							Deselect All
						</button>
					</div>
					<Badge variant="signal" size="sm">{$selectedCount}/{$activeCount}</Badge>
				</div>
			{/if}
		{/snippet}

		{#snippet empty()}
			{#if !current.selectedCategoryId}
				<div class="flex items-center justify-center h-full">
					<p class="text-sm text-fg-subtle">Select a category</p>
				</div>
			{:else}
				<div class="text-center py-8 px-4">
					<p class="text-sm text-fg-subtle">No values</p>
					<button
						type="button"
						class="mt-2 text-xs text-fg-muted hover:text-fg underline"
						onclick={() => phrasebookStore.handleNewValue()}
					>
						Add first value
					</button>
				</div>
			{/if}
		{/snippet}

		{#snippet children()}
			{#each values as value (value.id)}
				{#snippet preview()}
					<div class="bg-surface-2 rounded-lg shadow-floating border border-line p-1">
						<img
							src={api.getFileURL(value.preview_file_id as string, 'medium')}
							alt={value.label}
							class="w-48 h-48 rounded object-cover"
						/>
						<div class="px-2 py-1.5 text-xs text-fg-muted text-center truncate max-w-[192px]">
							{value.label}
						</div>
					</div>
				{/snippet}
				{#snippet actions()}
					<button
						type="button"
						class="p-1.5 rounded transition-colors {value.is_active
							? 'text-success hover:bg-success/10'
							: 'text-fg-subtle hover:bg-surface-3'}"
						title="{value.is_active ? 'Deactivate' : 'Activate'} value"
						onclick={(e) => {
							e.stopPropagation();
							phrasebookStore.handleToggleValueActive(value.id);
						}}
					>
						<Icon name={value.is_active ? 'check' : 'close'} className="w-4 h-4" />
					</button>
				{/snippet}
				<PaneRow
					checkable={value.is_active}
					checked={current.selectedValueIds.has(value.id)}
					onCheck={() => phrasebookStore.toggleValueSelection(value.id)}
					checkboxSpacer={!value.is_active}
					thumbnail={value.preview_file_id ? api.getFileURL(value.preview_file_id, 'small') : undefined}
					thumbSize="lg"
					thumbFallback={value.label}
					title={value.label}
					subtitle={value.value}
					subtitleMono
					selected={value.id === current.selectedValueId}
					inactive={!value.is_active}
					inactiveBadge="inactive"
					onclick={() => phrasebookStore.handleSelectValue(value.id)}
					{actions}
					preview={value.preview_file_id ? preview : undefined}
				/>
			{/each}
		{/snippet}
	</Pane>
</div>
