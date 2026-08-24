<script lang="ts" generics="T extends CollectionLike">
	import Icon from '$lib/components/Icon.svelte';
	import CollectionTreePicker from './CollectionTreePicker.svelte';
	import type { CollectionLike } from './types';

	export let collections: T[];
	export let open: boolean;
	// SelectionActionBar hosts this in a fixed bottom bar, where opening
	// upward is correct; PromptWorkspace hosts it in a detail-pane header
	// near the top, where opening upward clips the menu under the page
	// header — same up/down placement idiom as SearchableMultiSelectPopover.
	export let placement: 'up' | 'down' = 'up';
	export let onToggle: () => void;
	export let onClose: () => void;
	// Add the current selection to an existing collection. Resolves to whether
	// the caller should treat this as done (closes the menu) or leave it open
	// for a retry, matching the domain adapter's own success/failure feedback.
	export let onAdd: (collectionId: string) => Promise<boolean>;
	// Create a collection from the typed name, then add the selection to it.
	export let onCreateAndAdd: (name: string) => Promise<boolean>;

	let newCollectionName = '';
	let adding = false;
	let creating = false;

	async function handleAdd(collectionId: string) {
		adding = true;
		try {
			if (await onAdd(collectionId)) onClose();
		} finally {
			adding = false;
		}
	}

	async function handleCreateAndAdd() {
		const name = newCollectionName.trim();
		if (!name) return;
		creating = true;
		try {
			if (await onCreateAndAdd(name)) {
				newCollectionName = '';
				onClose();
			}
		} finally {
			creating = false;
		}
	}
</script>

<div class="relative">
	<button
		class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
		on:click={onToggle}
		aria-haspopup="menu"
		aria-expanded={open}
	>
		<Icon name="folder-plus" className="w-4 h-4" />
		Add to collection
		<Icon name={placement === 'up' ? 'chevron-up' : 'chevron-down'} className="w-3 h-3" />
	</button>
	{#if open}
		<div
			class="absolute {placement === 'up'
				? 'bottom-full mb-2'
				: 'top-full mt-2'} right-0 w-64 max-h-80 bg-surface-1 border border-line-strong rounded-lg shadow-overlay flex flex-col overflow-hidden z-30"
			role="menu"
		>
			<CollectionTreePicker
				{collections}
				disabled={adding}
				emptyMessage="No collections yet"
				class="flex-1"
				onSelect={(id) => id && handleAdd(id)}
			/>
			<div class="p-2 border-t border-line-strong/70 flex items-center gap-1">
				<input
					bind:value={newCollectionName}
					type="text"
					placeholder="New collection…"
					class="flex-1 min-w-0 px-2 py-1 text-xs bg-surface-2 border border-line-strong text-fg placeholder-fg-subtle rounded focus:outline-none focus:ring-1 focus:ring-signal"
					on:keydown={(e) => {
						if (e.key === 'Enter') handleCreateAndAdd();
					}}
				/>
				<button
					class="px-2 py-1 text-xs text-signal hover:bg-signal/10 rounded transition-colors disabled:opacity-50"
					disabled={creating || !newCollectionName.trim()}
					on:click={handleCreateAndAdd}
				>
					Create
				</button>
			</div>
		</div>
	{/if}
</div>
