<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import FavoriteButton from '$lib/components/FavoriteButton.svelte';
	import { Badge } from '$lib/components/ui';
	import CollectionTreePicker from '$lib/components/collections/CollectionTreePicker.svelte';

	export let modelType: string = '';
	export let displayName: string = '';
	export let customName: string | null = '';
	export let isFavorite: boolean = false;
	export let collections: any[] = [];
	/** Async so the "adding…" disabled state reflects the real request, not just the dispatch. */
	export let onAddToCollection: (collectionId: string) => Promise<void> = async () => {};
	/** Favorite + add-to-collection are user-library actions; the admin modal opts out. */
	export let showLibraryActions: boolean = true;

	const dispatch = createEventDispatcher<{
		rename: string;
		toggleFavorite: void;
	}>();

	let isEditingName = false;
	let nameValue = '';
	let collectionMenuOpen = false;
	let addingToCollection = false;

	function startEditName() {
		nameValue = customName || '';
		isEditingName = true;
	}

	function commitName() {
		isEditingName = false;
		dispatch('rename', nameValue.trim());
	}

	function toggleCollectionMenu(event: Event) {
		event.stopPropagation();
		collectionMenuOpen = !collectionMenuOpen;
	}

	async function addToCollection(collectionId: string) {
		addingToCollection = true;
		try {
			await onAddToCollection(collectionId);
		} finally {
			addingToCollection = false;
			collectionMenuOpen = false;
		}
	}

</script>

<Badge variant="neutral" class="font-mono uppercase tracking-wide flex-shrink-0">
	{modelType}
</Badge>
{#if isEditingName}
	<!-- svelte-ignore a11y-autofocus -->
	<input
		autofocus
		bind:value={nameValue}
		type="text"
		placeholder="Custom name…"
		class="min-w-0 flex-1 max-w-xs px-1.5 py-0.5 text-sm bg-surface-2 border border-line-strong text-fg rounded focus:outline-none focus:ring-1 focus:ring-signal"
		on:keydown={(e) => {
			if (e.key === 'Enter') commitName();
			if (e.key === 'Escape') isEditingName = false;
		}}
		on:blur={commitName}
	/>
{:else}
	<button
		class="text-sm text-fg-muted hover:text-fg truncate flex items-center gap-1.5 min-w-0"
		title="Click to set a custom name"
		on:click={startEditName}
	>
		<span class="truncate">{displayName}</span>
		<Icon name="pencil" className="w-3 h-3 flex-shrink-0 opacity-60" />
	</button>
{/if}
<div class="flex-1"></div>
{#if showLibraryActions}
<div class="relative flex-shrink-0">
	<button
		class="w-7 h-7 flex items-center justify-center rounded text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors"
		on:click={toggleCollectionMenu}
		aria-label="Add to collection"
		aria-haspopup="menu"
		aria-expanded={collectionMenuOpen}
	>
		<Icon name="folder-plus" className="w-4 h-4" />
	</button>
	{#if collectionMenuOpen}
		<div
			class="absolute right-0 top-full mt-1 z-50 w-52 bg-surface-1 border border-line-strong rounded-lg shadow-floating py-1 text-left"
			role="menu"
			tabindex="-1"
		>
			<div class="px-2 py-1 text-2xs uppercase tracking-wide text-fg-subtle">Add to…</div>
			<CollectionTreePicker
				{collections}
				disabled={addingToCollection}
				emptyMessage="No collections yet."
				class="max-h-48"
				onSelect={(id) => id && addToCollection(id)}
			/>
		</div>
	{/if}
</div>
<div class="w-7 h-7 flex items-center justify-center rounded flex-shrink-0">
	<FavoriteButton active={isFavorite} size="md" onToggle={() => dispatch('toggleFavorite')} />
</div>
{/if}

<svelte:window on:click={() => collectionMenuOpen && (collectionMenuOpen = false)} />
