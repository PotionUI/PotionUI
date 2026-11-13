<script lang="ts">
	import { onMount } from 'svelte';
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { inspirationsCollectionsStore } from '$lib/stores/inspirationsCollections';
	import InspirationsToolbar from './components/InspirationsToolbar.svelte';
	import InspirationsSidebar from './components/InspirationsSidebar.svelte';
	import InspirationsGrid from './components/InspirationsGrid.svelte';
	import InspirationDetailModal from './components/InspirationDetailModal.svelte';
	import type { InspirationDto } from '$lib/services/api/inspirations';

	let sidebarOpen = true;

	$: state = $inspirationsStore;
	// Derived by id (not held as a standalone copy) so a store patch after
	// save/comment mutations - which only touch `items` - flows straight
	// through to the modal instead of leaving it pinned to a stale snapshot.
	$: selected = state.items.find((item) => item.id === state.selectedId) ?? null;

	onMount(() => {
		inspirationsStore.load();
		inspirationsCollectionsStore.load();
	});

	function openItem(item: InspirationDto) {
		inspirationsStore.setSelectedId(item.id);
	}

	function closeModal() {
		inspirationsStore.setSelectedId(null);
	}

	function handleDeleted(id: string) {
		if (state.selectedId === id) inspirationsStore.setSelectedId(null);
	}
</script>

<svelte:head>
	<title>Inspirations · PotionUI</title>
</svelte:head>

<div class="flex h-[calc(100dvh-var(--tab-bar-height))] md:h-[100dvh] bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the grid scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-[272px] flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<InspirationsSidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
		</aside>
	{/if}

	<div class="flex-1 min-w-0 min-h-0 overflow-y-auto flex flex-col">
		<div class="sticky top-0 z-30">
			<InspirationsToolbar {sidebarOpen} onOpenSidebar={() => (sidebarOpen = true)} />
		</div>

		<InspirationsGrid onOpen={openItem} />
	</div>
</div>

{#if selected}
	<InspirationDetailModal item={selected} onClose={closeModal} onDeleted={handleDeleted} />
{/if}
