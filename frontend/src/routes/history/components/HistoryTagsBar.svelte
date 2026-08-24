<script lang="ts">
	import { historyStore } from '$lib/stores/history';
	import QuickTagFilterBar from '$lib/components/QuickTagFilterBar.svelte';
	import TagFilterOverflowMenu from '$lib/components/TagFilterOverflowMenu.svelte';

	// Self-contained: reads/writes historyStore directly. The overflow dropdown
	// is the shared TagFilterOverflowMenu (also used by the library tags bar).
	$: currentState = $historyStore;
	$: availableTags = currentState.availableTags;

	async function handleFilterChange() {
		await historyStore.loadGenerations();
	}

	function handleTagToggle(tagId: string) {
		const isSelected = currentState.filters.selectedTagIds.includes(tagId);
		if (isSelected) {
			historyStore.removeTagFilter(tagId);
		} else {
			historyStore.addTagFilter(tagId);
		}
		handleFilterChange();
	}

	function handleClearTags() {
		currentState.filters.selectedTagIds.forEach((tagId) => {
			historyStore.removeTagFilter(tagId);
		});
		handleFilterChange();
	}
</script>

<QuickTagFilterBar
	tags={availableTags}
	selectedIds={currentState.filters.selectedTagIds}
	onToggle={handleTagToggle}
	onClear={handleClearTags}
>
	<svelte:fragment slot="overflow" let:overflowTags>
		<TagFilterOverflowMenu
			tags={overflowTags}
			selectedIds={currentState.filters.selectedTagIds}
			onToggle={handleTagToggle}
		/>
	</svelte:fragment>
</QuickTagFilterBar>
