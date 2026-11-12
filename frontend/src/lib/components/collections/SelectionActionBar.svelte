<script lang="ts" generics="T extends CollectionLike">
	import { IconButton } from '$lib/components/ui';
	import { fly, fade } from 'svelte/transition';
	import { quartOut } from 'svelte/easing';
	import dismiss from '$lib/actions/dismiss';
	import type { CollectionLike } from './types';
	import AddToCollectionMenu from './AddToCollectionMenu.svelte';

	export let active: boolean;
	export let selectedCount: number;
	export let totalCount: number;
	export let onSelectAll: () => void;
	export let onClearSelection: () => void;
	export let onClose: () => void;
	export let feedback: string | null = null;
	export let collections: T[] | undefined = undefined;
	export let onAddToCollection: ((collectionId: string) => Promise<boolean>) | undefined = undefined;
	export let onCreateAndAddToCollection: ((name: string) => Promise<boolean>) | undefined = undefined;
	/** "Select all N matching" - shown once every loaded item is already
	 *  selected and more exist beyond the loaded page (Gmail's pattern).
	 *  Optional: only the history grid currently supports it. */
	/** Muted keyboard-shortcut hints for the selection gestures (shift-range,
	 *  ctrl/cmd-toggle, marquee drag). Optional, hidden on narrow screens. */

	// Which dropdown (if any) is open. Shared across the built-in collection
	// menu and any domain menu rendered into the action slots, so opening one
	// closes the other - same as a native menubar.
	let activeMenu: string | null = null;
	let barEl: HTMLDivElement;

	function toggleMenu(id: string) {
		activeMenu = activeMenu === id ? null : id;
	}

	function closeMenus() {
		activeMenu = null;
	}
</script>

{#if active}
	{#if activeMenu}
		<span hidden use:dismiss={{ onDismiss: closeMenus, ignore: [barEl] }}></span>
	{/if}
	<div class="fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50 w-max max-w-[calc(100vw-2rem)]">
		<div
			class="bg-surface-1 text-fg rounded-xl shadow-overlay px-2 py-1.5 flex items-center gap-1 border border-line-strong"
			in:fly={{ y: 10, duration: 200, easing: quartOut }}
			out:fade={{ duration: 150 }}
			on:click|stopPropagation
			on:keydown|stopPropagation
			role="toolbar"
			tabindex="-1"
			bind:this={barEl}
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
						class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						on:click={onSelectAll}
					>
						Select All
					</button>
				{/if}
				{#if selectedCount > 0}
					<button
						class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
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

					{#if collections && onAddToCollection && onCreateAndAddToCollection}
						<AddToCollectionMenu
							{collections}
							open={activeMenu === 'collection'}
							onToggle={() => toggleMenu('collection')}
							onClose={closeMenus}
							onAdd={onAddToCollection}
							onCreateAndAdd={onCreateAndAddToCollection}
						/>
					{/if}

					<slot name="actionsAfterCollection" {activeMenu} {toggleMenu} {closeMenus} />
				</div>
			{/if}

			<!-- Divider -->
			<div class="w-px h-6 bg-line-strong"></div>

			<!-- Close Button -->
			<IconButton icon="close" label="Exit selection mode" size="sm" onclick={onClose} />
		</div>
	</div>
{/if}
