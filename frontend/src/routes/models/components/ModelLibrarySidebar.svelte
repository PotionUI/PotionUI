<script lang="ts">
	import { onMount } from 'svelte';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let activeCollectionId: string | undefined;
	export let favoritesOnly: boolean;
	export let onSelectAll: () => void;
	export let onSelectFavorites: () => void;
	export let onSelectCollection: (id: string) => void;
	export let onCollapse: () => void;

	$: collections = $modelLibraryStore.collections;
	$: isAll = !activeCollectionId && !favoritesOnly;

	onMount(() => {
		modelLibraryStore.load();
	});

	// Post-delete fallback: if the deleted folder (or one of its now-gone
	// descendants) was the active filter, fall back to "all models".
	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await modelLibraryStore.remove(id);
		if (response.success && activeCollectionId && blockedIds.has(activeCollectionId)) {
			onSelectCollection('');
		}
		return response;
	}

	$: smartViews = [
		{ id: 'all', icon: 'model', label: 'All models', active: isAll, onSelect: onSelectAll },
		{
			id: 'favorites',
			icon: 'star',
			label: 'Favorites',
			active: favoritesOnly,
			onSelect: onSelectFavorites
		}
	] satisfies SmartView[];

	const treeActions: TreeActions = {
		onSelect: onSelectCollection,
		onRename: (id, name) => modelLibraryStore.rename(id, name),
		onCreate: (name, parentId) => modelLibraryStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => modelLibraryStore.move(id, parentId),
		onBulkMove: (ids, parentId) => modelLibraryStore.bulkMove(ids, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="models-expanded-collections"
	{collections}
	activeId={activeCollectionId}
	{smartViews}
	{treeActions}
	onCreateRoot={(name) => modelLibraryStore.create(name, null)}
	{onCollapse}
/>
