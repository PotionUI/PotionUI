<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { buildTree, flattenTree } from '$lib/components/pane';
	import { filterBySearch } from '$lib/components/selectors/searchFilter';
	import SearchableMultiSelectPopover from '$lib/components/selectors/SearchableMultiSelectPopover.svelte';

	type Node = { id: string; name: string; parent_id?: string | null };

	const ALL = '__all__';
	const UNSORTED = '__unsorted__';

	let {
		collections,
		loading = false,
		collectionId = null,
		unsorted = false,
		offersUnsorted = false,
		onOpen,
		onChange
	}: {
		collections: Node[];
		loading?: boolean;
		collectionId?: string | null;
		unsorted?: boolean;
		offersUnsorted?: boolean;
		onOpen?: () => void;
		onChange: (next: { collectionId: string | null; unsorted: boolean }) => void;
	} = $props();

	let open = $state(false);
	let search = $state('');

	let nodes = $derived(filterBySearch(flattenTree(buildTree(collections as never)) as never[], search, (node: any) => node.item.name) as any[]);
	let optionIds = $derived([ALL, ...(offersUnsorted ? [UNSORTED] : []), ...nodes.map((node) => node.item.id as string)]);
	let active = $derived(!!collectionId || unsorted);
	let label = $derived(unsorted ? 'Unsorted' : collectionId ? (collections.find((entry) => entry.id === collectionId)?.name ?? '…') : 'Collection');

	function pick(id: string) {
		open = false;
		if (id === ALL) onChange({ collectionId: null, unsorted: false });
		else if (id === UNSORTED) onChange({ collectionId: null, unsorted: true });
		else onChange({ collectionId: id, unsorted: false });
	}

	const rowClass = (selected: boolean, current: boolean) =>
		`flex w-full items-center gap-2 py-1.5 pr-3 text-left text-sm transition-colors ${selected ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-3/50'} ${current ? 'ring-1 ring-inset ring-signal/50' : ''}`;
</script>

<SearchableMultiSelectPopover
	bind:open
	bind:searchValue={search}
	panelClass="w-64"
	searchPlaceholder="Search collections..."
	{optionIds}
	{onOpen}
	onSelect={pick}
>
	{#snippet trigger({ open: isOpen, toggle })}
		<div class="flex items-center gap-1.5 flex-wrap">
			<button
				type="button"
				class="px-3 py-1.5 text-sm font-medium rounded flex items-center gap-1 max-w-[14rem] {active ? 'bg-signal/10 text-signal' : 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
				onclick={(event) => {
					event.stopPropagation();
					toggle();
				}}
				aria-haspopup="listbox"
				aria-expanded={isOpen}
				data-collection-filter
			>
				<Icon name="folder" className="w-4 h-4 flex-shrink-0" />
				<span class="truncate">{label}</span>
			</button>
			{#if active}
				<button
					type="button"
					class="px-2 py-1 text-sm font-medium text-danger hover:bg-surface-3 rounded"
					onclick={(event) => {
						event.stopPropagation();
						pick(ALL);
					}}
				>
					Clear
				</button>
			{/if}
		</div>
	{/snippet}

	{#snippet panel({ activeId, optionId, listboxId })}
		<div id={listboxId} class="max-h-64 overflow-y-auto py-1" role="listbox" aria-label="Collections">
			<button type="button" id={optionId(ALL)} class={rowClass(!active, activeId === ALL)} style="padding-left: 0.75rem" onclick={() => pick(ALL)} role="option" aria-selected={!active}>
				All items
			</button>
			{#if offersUnsorted}
				<button type="button" id={optionId(UNSORTED)} class={rowClass(unsorted, activeId === UNSORTED)} style="padding-left: 0.75rem" onclick={() => pick(UNSORTED)} role="option" aria-selected={unsorted}>
					Unsorted
				</button>
			{/if}
			{#if loading && collections.length === 0}
				<p class="px-3 py-2 text-center text-sm text-fg-subtle">Loading collections…</p>
			{:else if nodes.length === 0}
				<p class="px-3 py-2 text-center text-sm text-fg-subtle">{collections.length === 0 ? 'No collections yet' : 'No collections found'}</p>
			{:else}
				{#each nodes as node (node.item.id)}
					<button
						type="button"
						id={optionId(node.item.id)}
						class={rowClass(collectionId === node.item.id, activeId === node.item.id)}
						style="padding-left: {0.75 + node.depth * 0.9}rem"
						onclick={() => pick(node.item.id)}
						role="option"
						aria-selected={collectionId === node.item.id}
					>
						<Icon name="folder" className="w-3.5 h-3.5 flex-shrink-0" />
						<span class="truncate">{node.item.name}</span>
					</button>
				{/each}
			{/if}
		</div>
	{/snippet}
</SearchableMultiSelectPopover>
