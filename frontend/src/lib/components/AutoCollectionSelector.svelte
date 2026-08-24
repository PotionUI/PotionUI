<script lang="ts">
	import { historyCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { buildTree, flattenTree } from '$lib/components/pane';
	import { filterBySearch } from '$lib/components/selectors/searchFilter';
	import Icon from '$lib/components/Icon.svelte';
	import SearchableMultiSelectPopover from '$lib/components/selectors/SearchableMultiSelectPopover.svelte';

	export let selectedCollectionIds: string[] = [];

	let isOpen = false;
	let searchValue = '';
	let loaded = false;

	$: collections = $collectionsStore.collections;
	$: collectionNameMap = new Map(collections.map((collection) => [collection.id, collection.name]));
	$: flattenedCollections = flattenTree(buildTree(collections));
	$: filteredCollections = filterBySearch(flattenedCollections, searchValue, (node) => node.item.name);
	$: optionIds = filteredCollections.map((node) => node.item.id);

	async function handlePopoverOpen() {
		if (loaded) return;
		loaded = true;
		await collectionsStore.load();
	}

	function toggleCollection(collectionId: string) {
		selectedCollectionIds = selectedCollectionIds.includes(collectionId)
			? selectedCollectionIds.filter((id) => id !== collectionId)
			: [...selectedCollectionIds, collectionId];
	}

	function removeCollection(collectionId: string, event?: MouseEvent) {
		event?.stopPropagation();
		selectedCollectionIds = selectedCollectionIds.filter((id) => id !== collectionId);
	}
</script>

<SearchableMultiSelectPopover
	bind:open={isOpen}
	bind:searchValue
	panelClass="min-w-56"
	searchPlaceholder="Search collections…"
	{optionIds}
	onOpen={handlePopoverOpen}
	onSelect={toggleCollection}
>
	{#snippet trigger({ open, toggle })}
		<div
			class="flex min-h-8 min-w-44 cursor-pointer select-none items-center gap-1 rounded border border-line-strong bg-surface-2 px-2 py-1 text-xs text-fg transition-colors hover:border-line-hover hover:bg-surface-3"
			on:click|stopPropagation={toggle}
			on:keydown={(event) => {
				if (event.key === 'Enter' || event.key === ' ') {
					event.preventDefault();
					toggle();
				}
			}}
			role="button"
			tabindex="0"
			aria-haspopup="listbox"
			aria-expanded={open}
		>
			{#if selectedCollectionIds.length > 0}
				<div class="flex min-w-0 flex-1 flex-wrap gap-1">
					{#each selectedCollectionIds as collectionId (collectionId)}
						<span class="inline-flex max-w-40 items-center gap-1 rounded border border-line-strong bg-surface-3 px-1.5 py-0.5 text-[0.7rem] text-fg-muted">
							<span class="truncate">{collectionNameMap.get(collectionId) || (loaded ? collectionId : '…')}</span>
							<button
								type="button"
								class="text-fg-subtle transition-colors hover:text-danger"
								on:click|stopPropagation={(e) => removeCollection(collectionId, e)}
								aria-label="Remove auto-collection"
							>×</button>
						</span>
					{/each}
				</div>
			{:else}
				<span class="min-w-0 flex-1 text-fg-subtle">Auto-collections…</span>
			{/if}
			<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
		</div>
	{/snippet}

	{#snippet panel({ activeId, optionId, listboxId })}
		<div id={listboxId} class="max-h-52 overflow-y-auto py-1" role="listbox" aria-label="Auto-collections" aria-multiselectable="true">
			{#if $collectionsStore.loading && collections.length === 0}
				<p class="px-3 py-2 text-center text-xs text-fg-subtle">Loading collections…</p>
			{:else if filteredCollections.length === 0}
				<p class="px-3 py-2 text-center text-xs text-fg-subtle">
					{collections.length === 0 ? 'No collections yet' : 'No collections found'}
				</p>
			{:else}
				{#each filteredCollections as node (node.item.id)}
					<button
						type="button"
						id={optionId(node.item.id)}
						class="flex w-full items-center gap-2 py-1.5 pr-3 text-left text-xs transition-colors {selectedCollectionIds.includes(node.item.id) ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'} {activeId === node.item.id ? 'ring-1 ring-inset ring-signal/50' : ''}"
						style="padding-left: {0.75 + node.depth * 0.9}rem"
						on:click={() => toggleCollection(node.item.id)}
						role="option"
						aria-selected={selectedCollectionIds.includes(node.item.id)}
					>
						<span class="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-sm border border-line-strong text-[0.6rem] {selectedCollectionIds.includes(node.item.id) ? 'border-signal-solid bg-signal-solid text-white' : ''}">
							{#if selectedCollectionIds.includes(node.item.id)}✓{/if}
						</span>
						<Icon name="folder" className="h-3.5 w-3.5 flex-shrink-0" />
						<span class="truncate">{node.item.name}</span>
					</button>
				{/each}
			{/if}
		</div>
	{/snippet}
</SearchableMultiSelectPopover>
