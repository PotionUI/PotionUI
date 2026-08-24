<script lang="ts">
	import { libraryStore } from '$lib/stores/library';
	import QuickTagFilterBar from '$lib/components/QuickTagFilterBar.svelte';
	import TagFilterOverflowMenu from '$lib/components/TagFilterOverflowMenu.svelte';

	// Self-contained: reads/writes libraryStore directly. Same quick-filter row
	// and overflow menu as the history tags bar, driven by UPLOAD tags.
	$: state = $libraryStore;

	async function handleTagToggle(tagId: string) {
		libraryStore.toggleTagFilter(tagId);
		await libraryStore.load();
	}

	async function handleClearTags() {
		libraryStore.clearTagFilters();
		await libraryStore.load();
	}
</script>

<QuickTagFilterBar
	tags={state.availableTags}
	selectedIds={state.filters.selectedTagIds}
	onToggle={handleTagToggle}
	onClear={handleClearTags}
>
	<svelte:fragment slot="overflow" let:overflowTags>
		<TagFilterOverflowMenu
			tags={overflowTags}
			selectedIds={state.filters.selectedTagIds}
			onToggle={handleTagToggle}
		/>
	</svelte:fragment>
</QuickTagFilterBar>
