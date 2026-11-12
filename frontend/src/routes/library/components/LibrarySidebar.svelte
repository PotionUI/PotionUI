<script lang="ts">
	import { libraryCollectionsStore as collectionsStore } from '$lib/stores/collections';
	import { libraryStore } from '$lib/stores/library';
	import CollectionLibrarySidebar from '$lib/components/collections/CollectionLibrarySidebar.svelte';
	import CollectionActionsMenu from '$lib/components/collections/CollectionActionsMenu.svelte';
	import type { SmartView, TreeActions } from '$lib/components/collections/types';
	import { buildSmartViews, createFilterSelection } from '$lib/components/collections/scopeActions';
	import { createEntriesActions } from '$lib/components/collections/deleteEntries';
	import { api } from '$lib/services/api/index';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { kitTotal, loadKitTotal } from '$lib/stores/kits';
	import { hrefWithKitsView, kitsViewFromUrl } from '$lib/kits/libraryView';
	import { onMount } from 'svelte';

	let allView: SmartView;
	let favoritesView: SmartView;
	let unsortedView: SmartView;
	let kitsView: SmartView;

	export let onCollapse: (() => void) | undefined = undefined;
	export let part: 'sidebar' | 'actions' = 'sidebar';

	$: collections = $collectionsStore.collections;
	$: smartCounts = $collectionsStore.smartCounts;
	$: activeId = $libraryStore.filters.collectionId;
	$: activeCollection = collections.find((c) => c.id === activeId);
	$: favoritesOnly = !!$libraryStore.filters.favoritesOnly;
	$: unsorted = !!$libraryStore.filters.unsorted;
	$: directOnly = !!$libraryStore.filters.directOnly;
	$: kitsActive = kitsViewFromUrl($page.url).active;
	$: isAll = !activeId && !favoritesOnly && !unsorted && !kitsActive;
	$: void collectionsStore.setDirectOnly(directOnly);

	function leaveKits() {
		if (!kitsActive) return;
		void goto(hrefWithKitsView($page.url, { active: false, kitId: null }), {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	function showKits() {
		void goto(hrefWithKitsView($page.url, { active: true, kitId: null }), { keepFocus: true, noScroll: true });
	}

	onMount(() => {
		if ($kitTotal === null) void loadKitTotal();
	});

	const { applySelection: applyFilterSelection } = createFilterSelection(
		(key, value) => libraryStore.setFilter(key, value),
		() => libraryStore.load()
	);

	function applySelection(selection: Parameters<typeof applyFilterSelection>[0]) {
		leaveKits();
		return applyFilterSelection(selection);
	}

	async function handleDelete(id: string, blockedIds: Set<string>) {
		const response = await collectionsStore.remove(id);
		if (response.success) {
			const active = $libraryStore.filters.collectionId;
			if (active && blockedIds.has(active)) await applySelection({});
		}
		return response;
	}

	$: ({ allView, favoritesView, unsortedView } = buildSmartViews({
		icon: 'photo',
		label: 'All uploads',
		isAll,
		favoritesOnly: favoritesOnly && !kitsActive,
		unsorted: unsorted && !kitsActive,
		counts: smartCounts,
		onAll: () => applySelection({}),
		onFavorites: () => applySelection({ favoritesOnly: true }),
		onUnsorted: () => applySelection({ unsorted: true })
	}));

	$: kitsView = {
		id: 'kits',
		icon: 'layers',
		label: 'Kits',
		active: kitsActive,
		count: $kitTotal ?? undefined,
		onSelect: showKits
	};

	const treeActions: TreeActions = {
		onSelect: (id) => applySelection({ collectionId: id }),
		onRename: (id, name) => collectionsStore.rename(id, name),
		onCreate: (name, parentId) => collectionsStore.create(name, parentId),
		onDelete: handleDelete,
		onMove: (id, parentId) => collectionsStore.move(id, parentId),
		onBulkMove: (ids, parentId) => collectionsStore.bulkMove(ids, parentId),
		entries: createEntriesActions(
			'library',
			{ list: (scope, directOnly) => api.listCollections(scope, directOnly), deleteEntries: (id) => api.deleteCollectionEntries(id) },
			() => Promise.all([libraryStore.load(), libraryStore.loadFacets(), collectionsStore.load()])
		)
	};
</script>

{#if part === 'actions'}
	{#if activeCollection}
		<CollectionActionsMenu
			variant="toolbar"
			item={activeCollection}
			{collections}
			{treeActions}
			rulesScope="library"
		/>
	{/if}
{:else}
	<CollectionLibrarySidebar
		storageKey="library-expanded-collections"
		label="Library"
		rulesScope="library"
		{collections}
		activeId={kitsActive ? undefined : activeId}
		{allView}
		{favoritesView}
		{unsortedView}
		{kitsView}
		autoOrganizeSubject="uploads"
		{treeActions}
		onCreateRoot={(name) => collectionsStore.create(name, null)}
		{onCollapse}
	/>
{/if}
