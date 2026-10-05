<script lang="ts">
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { inspirationsCollectionsStore } from '$lib/stores/inspirationsCollections';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';

	let { onCollapse }: { onCollapse: () => void } = $props();

	let collections = $derived($inspirationsCollectionsStore.collections);
	let smartCounts = $derived($inspirationsCollectionsStore.smartCounts);
	let filters = $derived($inspirationsStore.filters);
	let activeId = $derived(filters.collectionId);
	let directOnly = $derived(!!filters.directOnly);

	$effect(() => {
		void inspirationsCollectionsStore.setDirectOnly(directOnly);
	});

	async function applySelection(patch: {
		collectionId?: string;
		saved?: boolean;
		unsorted?: boolean;
	}) {
		inspirationsStore.setFilter('collectionId', patch.collectionId);
		inspirationsStore.setFilter('saved', !!patch.saved);
		inspirationsStore.setFilter('unsorted', !!patch.unsorted);
		await inspirationsStore.load();
	}

	async function setDirectOnly(value: boolean) {
		inspirationsStore.setFilter('directOnly', value);
		await inspirationsStore.load();
	}

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await inspirationsCollectionsStore.remove(id);
		if (response.success) {
			const active = $inspirationsStore.filters.collectionId;
			if (active && blockedIds.has(active)) await applySelection({});
		}
		return response;
	}

	let allView = $derived({
		id: 'all',
		icon: 'photo',
		label: 'All inspirations',
		active: !filters.collectionId && !filters.saved && !filters.unsorted,
		count: smartCounts?.all,
		onSelect: () => applySelection({})
	} satisfies SmartView);
	let favoritesView = $derived({
		id: 'favorites',
		icon: 'heart',
		label: 'Favorites',
		active: !!filters.saved,
		count: smartCounts?.favorites,
		onSelect: () => applySelection({ saved: true })
	} satisfies SmartView);
	let unsortedView = $derived({
		id: 'unsorted',
		icon: 'inbox',
		label: 'Unsorted',
		active: !!filters.unsorted,
		count: smartCounts?.unsorted,
		onSelect: () => applySelection({ unsorted: true })
	} satisfies SmartView);

	const treeActions: TreeActions = {
		onSelect: (id) => applySelection({ collectionId: id }),
		onRename: (id, name) => inspirationsCollectionsStore.rename(id, name),
		onCreate: (name, parentId) => inspirationsCollectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => inspirationsCollectionsStore.move(id, parentId)
	};
</script>

<CollectionLibrarySidebar
	storageKey="inspirations-expanded-collections"
	label="Inspirations"
	{collections}
	{activeId}
	{allView}
	{favoritesView}
	{unsortedView}
	{directOnly}
	onDirectOnlyChange={setDirectOnly}
	{treeActions}
	onCreateRoot={(name) => inspirationsCollectionsStore.create(name, null)}
	{onCollapse}
/>
