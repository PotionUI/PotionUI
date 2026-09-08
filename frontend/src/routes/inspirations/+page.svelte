<script lang="ts">
	import { onMount } from 'svelte';
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { inspirationsCollectionsStore } from '$lib/stores/inspirationsCollections';
	import Icon from '$lib/components/Icon.svelte';
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

<div class="flex min-h-screen bg-canvas">
	<!-- Left folder-tree panel (collapsible), pinned while the grid scrolls -->
	{#if sidebarOpen}
		<aside
			class="hidden md:block w-60 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<div class="sticky top-0 h-screen overflow-hidden">
				<InspirationsSidebar onCollapse={() => (sidebarOpen = false)} />
			</div>
		</aside>
	{:else}
		<aside
			class="hidden md:block w-8 flex-shrink-0 self-stretch min-h-screen border-r border-line bg-surface-1 z-20"
		>
			<button
				class="sticky top-0 flex h-screen w-full flex-col items-center gap-2 pt-3 text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
				on:click={() => (sidebarOpen = true)}
				title="Show folders"
				aria-label="Show folders"
			>
				<Icon name="chevron-right" className="w-4 h-4" />
				<Icon name="folder" className="w-4 h-4" />
			</button>
		</aside>
	{/if}

	<div class="flex-1 min-w-0">
		<div class="sticky top-0 z-30">
			<InspirationsToolbar />
		</div>

		<InspirationsGrid onOpen={openItem} />
	</div>
</div>

{#if selected}
	<InspirationDetailModal item={selected} onClose={closeModal} onDeleted={handleDeleted} />
{/if}
