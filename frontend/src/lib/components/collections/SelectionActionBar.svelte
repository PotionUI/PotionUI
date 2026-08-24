<script lang="ts" generics="T extends CollectionLike">
	import { fly, fade } from 'svelte/transition';
	import { quartOut } from 'svelte/easing';
	import Icon from '$lib/components/Icon.svelte';
	import type { CollectionLike } from './types';
	import AddToCollectionMenu from './AddToCollectionMenu.svelte';

	export let active: boolean;
	export let selectedCount: number;
	export let totalCount: number;
	export let onSelectAll: () => void;
	export let onClearSelection: () => void;
	export let onClose: () => void;
	export let feedback: string | null = null;
	export let collections: T[];
	export let onAddToCollection: (collectionId: string) => Promise<boolean>;
	export let onCreateAndAddToCollection: (name: string) => Promise<boolean>;

	// Which dropdown (if any) is open. Shared across the built-in collection
	// menu and any domain menu rendered into the action slots, so opening one
	// closes the other - same as a native menubar.
	let activeMenu: string | null = null;

	function toggleMenu(id: string) {
		activeMenu = activeMenu === id ? null : id;
	}

	function closeMenus() {
		activeMenu = null;
	}
</script>

<svelte:window on:click={() => activeMenu && closeMenus()} />

{#if active}
	<div class="fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50">
		<div
			class="bg-surface-1 text-fg rounded-xl shadow-overlay px-2 py-1.5 flex items-center gap-1 border border-line-strong"
			in:fly={{ y: 10, duration: 200, easing: quartOut }}
			out:fade={{ duration: 150 }}
			on:click|stopPropagation
			on:keydown|stopPropagation
			role="toolbar"
			tabindex="-1"
		>
			<!-- Selection Count -->
			<div class="px-3 py-1.5 whitespace-nowrap font-mono text-2xs uppercase tracking-[0.07em]">
				<span class="text-fg tabular-nums">{selectedCount}</span>
				<span class="text-fg-muted ml-1">selected</span>
			</div>

			{#if feedback}
				<div class="px-2 text-xs text-signal whitespace-nowrap">{feedback}</div>
			{/if}

			<!-- Divider -->
			<div class="w-px h-6 bg-line-strong"></div>

			<!-- Selection helpers -->
			<div class="flex items-center gap-1 px-1">
				{#if selectedCount < totalCount}
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						on:click={onSelectAll}
					>
						Select All
					</button>
				{/if}
				{#if selectedCount > 0}
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						on:click={onClearSelection}
					>
						Clear
					</button>
				{/if}
			</div>

			{#if selectedCount > 0}
				<!-- Divider -->
				<div class="w-px h-6 bg-line-strong"></div>

				<div class="flex items-center gap-1 px-1">
					<slot name="actionsBeforeCollection" {activeMenu} {toggleMenu} {closeMenus} />

					<AddToCollectionMenu
						{collections}
						open={activeMenu === 'collection'}
						onToggle={() => toggleMenu('collection')}
						onClose={closeMenus}
						onAdd={onAddToCollection}
						onCreateAndAdd={onCreateAndAddToCollection}
					/>

					<slot name="actionsAfterCollection" {activeMenu} {toggleMenu} {closeMenus} />
				</div>
			{/if}

			<!-- Divider -->
			<div class="w-px h-6 bg-line-strong"></div>

			<!-- Close Button -->
			<button
				class="p-2 text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
				on:click={onClose}
				aria-label="Exit selection mode"
			>
				<Icon name="close" className="w-4 h-4" />
			</button>
		</div>
	</div>
{/if}
