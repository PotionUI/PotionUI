<script lang="ts">
	import { promptsCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let activeId: string | undefined;
	export let favoritesOnly = false;
	export let unsorted = false;
	export let directOnly = false;
	export let onSelectAll: () => void | Promise<void>;
	export let onSelectFavorites: () => void | Promise<void>;
	export let onSelectUnsorted: () => void | Promise<void>;
	export let onSelectFolder: (id: string) => void | Promise<void>;
	export let onDirectOnlyChange: (directOnly: boolean) => void;

	$: collections = $collectionsStore.collections;
	$: smartCounts = $collectionsStore.smartCounts;
	$: void collectionsStore.setDirectOnly(directOnly);

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success && activeId && blockedIds.has(activeId)) {
			await onSelectAll();
		}
		return response;
	}

	$: allView = {
		id: 'all',
		icon: 'document',
		label: 'All prompts',
		active: !activeId && !favoritesOnly && !unsorted,
		count: smartCounts?.all,
		onSelect: onSelectAll
	} satisfies SmartView;
	$: favoritesView = {
		id: 'favorites',
		icon: 'heart',
		label: 'Favorites',
		active: favoritesOnly,
		count: smartCounts?.favorites,
		onSelect: onSelectFavorites
	} satisfies SmartView;
	$: unsortedView = {
		id: 'unsorted',
		icon: 'inbox',
		label: 'Unsorted',
		active: unsorted,
		count: smartCounts?.unsorted,
		onSelect: onSelectUnsorted
	} satisfies SmartView;

	const treeActions: TreeActions = {
		onSelect: onSelectFolder,
		onRename: (id, name) => collectionsStore.rename(id, name),
		onCreate: (name, parentId) => collectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => collectionsStore.move(id, parentId),
		onBulkMove: (ids, parentId) => collectionsStore.bulkMove(ids, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="prompts-expanded-collections"
	label="Collections"
	embedded
	{collections}
	{activeId}
	{allView}
	{favoritesView}
	{unsortedView}
	{directOnly}
	{onDirectOnlyChange}
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
/>
