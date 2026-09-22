<script lang="ts">
	import type { Prompt } from '$lib/types/segments';
	import type { CollectionLike } from '$lib/components/collections/types';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import PromptCard from './PromptCard.svelte';
	import { promptFilterActiveCount, clearAllPromptFilters, type PromptFilters } from '$lib/prompts/promptFilters';

	let {
		prompts,
		loading,
		loadingMore = false,
		hasMore = false,
		filters,
		selectedIds,
		collections,
		onFiltersChange,
		onLoadMore,
		onOpen,
		onUse,
		onCopy,
		onDuplicate,
		onAddToCollection,
		onExport,
		onDeleteOne,
		onToggleSelect,
		onSelectAll,
		onClearSelection,
		onBulkAddToCollection,
		onBulkCreateAndAddToCollection,
		onBulkAddTag,
		onBulkExport,
		onBulkDelete,
		onNewPrompt
	}: {
		prompts: Prompt[];
		loading: boolean;
		loadingMore?: boolean;
		hasMore?: boolean;
		filters: PromptFilters;
		selectedIds: Set<string>;
		collections: CollectionLike[];
		onFiltersChange: (filters: PromptFilters) => void;
		onLoadMore: () => void;
		onOpen: (prompt: Prompt) => void;
		onUse: (prompt: Prompt) => void;
		onCopy: (prompt: Prompt) => void;
		onDuplicate: (prompt: Prompt) => void;
		onAddToCollection: (prompt: Prompt) => void;
		onExport: (prompt: Prompt) => void;
		onDeleteOne: (prompt: Prompt) => void;
		onToggleSelect: (prompt: Prompt) => void;
		onSelectAll: () => void;
		onClearSelection: () => void;
		onBulkAddToCollection: (collectionId: string) => Promise<boolean>;
		onBulkCreateAndAddToCollection: (name: string) => Promise<boolean>;
		onBulkAddTag: () => void;
		onBulkExport: () => void;
		onBulkDelete: () => void;
		onNewPrompt: () => void;
	} = $props();

	let gridEl: HTMLDivElement | undefined = $state();

	const activeFilterCount = $derived(promptFilterActiveCount(filters));
	const filtered = $derived(activeFilterCount > 0 || !!filters.q);

	function clearAll() {
		onFiltersChange(clearAllPromptFilters(filters));
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && selectedIds.size > 0) onClearSelection();
	}

	function handleGridKeydown(event: KeyboardEvent) {
		if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
		const cards = Array.from(gridEl?.querySelectorAll<HTMLElement>('[data-prompt-card]') ?? []);
		const currentIndex = cards.indexOf(document.activeElement as HTMLElement);
		if (currentIndex === -1) return;
		event.preventDefault();
		const columns = columnsInFirstRow(cards);
		let nextIndex = currentIndex;
		if (event.key === 'ArrowRight') nextIndex = Math.min(cards.length - 1, currentIndex + 1);
		else if (event.key === 'ArrowLeft') nextIndex = Math.max(0, currentIndex - 1);
		else if (event.key === 'ArrowDown') nextIndex = Math.min(cards.length - 1, currentIndex + columns);
		else if (event.key === 'ArrowUp') nextIndex = Math.max(0, currentIndex - columns);
		cards[nextIndex]?.focus();
	}

	function columnsInFirstRow(cards: HTMLElement[]): number {
		if (cards.length < 2) return 1;
		const firstTop = cards[0].offsetTop;
		let count = 1;
		for (let i = 1; i < cards.length; i++) {
			if (cards[i].offsetTop !== firstTop) break;
			count++;
		}
		return count;
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="flex h-full flex-col overflow-hidden">
	<div class="min-h-0 flex-1 overflow-y-auto p-4">
		{#if loading}
			<div class="flex h-40 items-center justify-center">
				<Spinner size="lg" />
			</div>
		{:else if prompts.length === 0}
			<div class="flex h-full items-center justify-center">
				<EmptyState
					icon="document"
					title={filtered ? 'No prompts match these filters' : 'No prompts yet'}
					description={filtered
						? 'Try clearing a filter or broadening the search.'
						: 'Save your first prompt to start the library.'}
				>
					{#snippet actions()}
						{#if filtered}
							<Button size="sm" variant="ghost" onclick={clearAll}>Clear filters</Button>
						{:else}
							<Button size="sm" variant="primary" icon="plus" onclick={onNewPrompt}>Create a prompt</Button>
						{/if}
					{/snippet}
				</EmptyState>
			</div>
		{:else}
			<div
				bind:this={gridEl}
				class="grid grid-cols-[repeat(auto-fill,minmax(460px,1fr))] gap-3"
				role="toolbar"
				aria-label="Prompt cards"
				aria-orientation="horizontal"
				tabindex="-1"
				onkeydown={handleGridKeydown}
			>
				{#each prompts as prompt (prompt.id)}
					<PromptCard
						{prompt}
						selected={selectedIds.has(prompt.id)}
						{onToggleSelect}
						{onOpen}
						{onUse}
						{onCopy}
						{onDuplicate}
						{onAddToCollection}
						{onExport}
						onDelete={onDeleteOne}
					/>
				{/each}
			</div>

			{#if hasMore}
				<div class="mt-4 flex justify-center">
					<Button size="sm" variant="secondary" loading={loadingMore} onclick={onLoadMore}>
						Show 48 more
						<span class="ml-1.5 font-mono text-xs tabular-nums text-fg-subtle">{prompts.length} loaded</span>
					</Button>
				</div>
			{/if}
		{/if}
	</div>
</div>

<SelectionActionBar
	active={selectedIds.size > 0}
	selectedCount={selectedIds.size}
	totalCount={prompts.length}
	{onSelectAll}
	onClearSelection={onClearSelection}
	onClose={onClearSelection}
	feedback={null}
	{collections}
	onAddToCollection={onBulkAddToCollection}
	onCreateAndAddToCollection={onBulkCreateAndAddToCollection}
>
	<svelte:fragment slot="actionsAfterCollection">
		<Tooltip text="Add a tag to the selected prompts">
			<button
				class="flex items-center gap-1.5 rounded px-3 py-1.5 text-sm text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
				onclick={onBulkAddTag}
			>
				<Icon name="tag" className="h-4 w-4" />
				Add tag…
			</button>
		</Tooltip>
		<Tooltip text="Export the selected prompts">
			<button
				class="flex items-center gap-1.5 rounded px-3 py-1.5 text-sm text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
				onclick={onBulkExport}
			>
				<Icon name="download" className="h-4 w-4" />
				Export
			</button>
		</Tooltip>
		<button
			class="flex items-center gap-2 rounded bg-danger-solid px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-danger-solid/90"
			onclick={onBulkDelete}
		>
			<Icon name="trash" className="h-4 w-4" />
			Delete
		</button>
	</svelte:fragment>
</SelectionActionBar>
