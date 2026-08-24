<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { inspirationsCollectionsStore } from '$lib/stores/inspirationsCollections';
	import { toasts } from '$lib/stores/toast';

	export let inspirationId: string;
	export let onClose: () => void;

	$: collections = $inspirationsCollectionsStore.collections;

	let creating = false;
	let newName = '';
	let busyId: string | null = null;

	onMount(() => {
		if (collections.length === 0) inspirationsCollectionsStore.load();
	});

	async function addTo(collectionId: string) {
		busyId = collectionId;
		try {
			const response = await inspirationsCollectionsStore.addItem(collectionId, inspirationId);
			if (response.success) {
				toasts.success('Added to collection');
				onClose();
			} else {
				toasts.error(response.error ?? response.message ?? 'Could not add to that collection');
			}
		} finally {
			busyId = null;
		}
	}

	async function createAndAdd() {
		const name = newName.trim();
		if (!name) return;
		const response = await inspirationsCollectionsStore.create(name, null);
		const created = response.data?.collection;
		if (response.success && created) {
			await addTo(created.id);
		} else {
			toasts.error('Could not create that collection');
		}
	}
</script>

<div
	class="absolute right-0 top-full mt-1 z-50 w-56 bg-surface-1 border border-line-strong rounded-lg shadow-floating py-1"
	role="menu"
	tabindex="-1"
	on:click={(e) => e.stopPropagation()}
	on:keydown={(e) => e.stopPropagation()}
>
	<div class="px-2 py-1 text-2xs uppercase tracking-wide text-fg-subtle">Add to collection</div>
	<div class="max-h-56 overflow-y-auto">
		{#if collections.length === 0}
			<p class="px-2 py-1.5 text-xs text-fg-subtle">No collections yet.</p>
		{/if}
		{#each collections as c (c.id)}
			<button
				type="button"
				class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-1.5 disabled:opacity-50"
				disabled={busyId === c.id}
				on:click={() => addTo(c.id)}
				role="menuitem"
			>
				<Icon name="folder" className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
				<span class="truncate">{c.name}</span>
			</button>
		{/each}
	</div>
	<div class="my-1 h-px bg-line"></div>
	{#if creating}
		<div class="flex items-center gap-1 px-2 py-1">
			<!-- svelte-ignore a11y_autofocus -->
			<input
				autofocus
				bind:value={newName}
				type="text"
				placeholder="New collection…"
				class="flex-1 min-w-0 px-1.5 py-0.5 text-xs bg-surface-2 border border-line-strong text-fg placeholder-fg-subtle rounded focus:outline-none focus:ring-1 focus:ring-signal"
				on:keydown={(e) => {
					if (e.key === 'Enter') createAndAdd();
					if (e.key === 'Escape') creating = false;
				}}
			/>
		</div>
	{:else}
		<button
			type="button"
			class="w-full text-left px-2 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-surface-2 flex items-center gap-2"
			on:click={() => (creating = true)}
			role="menuitem"
		>
			<Icon name="folder-plus" className="w-3.5 h-3.5" /> New collection
		</button>
	{/if}
</div>
