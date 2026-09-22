<script lang="ts">
	import { promptsCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	export let activeId: string | undefined;
	export let onSelectAll: () => void | Promise<void>;
	export let onSelectFolder: (id: string) => void | Promise<void>;

	$: collections = $collectionsStore.collections;

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success && activeId && blockedIds.has(activeId)) {
			await onSelectAll();
		}
		return response;
	}

	$: smartViews = [
		{
			id: 'all',
			icon: 'document',
			label: 'All prompts',
			active: !activeId,
			onSelect: onSelectAll
		}
	] satisfies SmartView[];

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
	{smartViews}
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
/>
