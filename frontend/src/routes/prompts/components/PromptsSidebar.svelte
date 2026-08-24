<script lang="ts">
	import { promptsCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	// Unlike History/Library, the Prompt Library keeps its active-filter state
	// on the page itself (PromptWorkspace has no dedicated store) - so the
	// active collection id and the select callbacks come in as props instead
	// of being read off a shared filter store.
	export let activeId: string | undefined;
	export let onSelectAll: () => void | Promise<void>;
	export let onSelectFolder: (id: string) => void | Promise<void>;
	export let onCollapse: () => void;

	$: collections = $collectionsStore.collections;

	// Post-delete fallback: if the deleted folder (or one of its now-gone
	// descendants) was the active filter, fall back to "all prompts".
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
	{collections}
	{activeId}
	{smartViews}
	{treeActions}
	onCreateRoot={(name) => collectionsStore.create(name, null)}
	{onCollapse}
/>
