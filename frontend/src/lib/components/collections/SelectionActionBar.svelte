<script lang="ts" generics="T extends CollectionLike">
	import { tick } from 'svelte';
	import { IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import responsiveBar from './selectionBarResponsive';
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
	let actionsEl: HTMLDivElement;
	let mode: 'inline' | 'overflow' = 'inline';
	let moreOpen = false;

	$: if (mode === 'inline') moreOpen = false;

	function onMode(next: 'inline' | 'overflow') {
		mode = next;
	}

	async function toggleMore() {
		closeMenus();
		moreOpen = !moreOpen;
		if (!moreOpen) return;
		await tick();
		actionsEl?.querySelector<HTMLElement>('button')?.focus();
	}

	function onActionsClick(event: MouseEvent) {
		if (!moreOpen) return;
		const button = (event.target as HTMLElement).closest('button');
		if (button && !button.hasAttribute('aria-haspopup')) moreOpen = false;
	}

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
	{:else if moreOpen}
		<span hidden use:dismiss={{ onDismiss: () => (moreOpen = false), ignore: [barEl] }}></span>
	{/if}
	<div class="fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50 w-max max-w-[calc(100vw-2rem)]">
		<div
			class="relative bg-surface-1 text-fg rounded-xl shadow-overlay px-2 py-1.5 flex items-center gap-1 border border-line-strong"
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

			<div class="flex items-center gap-1 px-1">
				<span data-bar-more class="inline-flex" style="display: none">
					<IconButton icon="more" label="More actions" size="sm" ariaExpanded={moreOpen} onclick={toggleMore} />
				</span>
				<div
					bind:this={actionsEl}
					class="sel-actions flex items-center gap-1"
					role="presentation"
					data-open={moreOpen}
					use:responsiveBar={{ onMode }}
					on:click={onActionsClick}
				>
					{#if selectedCount < totalCount}
						<button
							class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
							on:click={onSelectAll}
						>
							<Icon name="check" className="w-4 h-4" />
							Select All
						</button>
					{/if}
					{#if selectedCount > 0}
						<button
							class="whitespace-nowrap px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
							on:click={onClearSelection}
						>
							<Icon name="close" className="w-4 h-4" />
							Clear
						</button>
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
					{/if}
				</div>
				{#if selectedCount > 0}
					<slot name="pinnedActions" {activeMenu} {toggleMenu} {closeMenus} />
				{/if}
			</div>

			<!-- Divider -->
			<div class="w-px h-6 bg-line-strong"></div>

			<!-- Close Button -->
			<IconButton icon="close" label="Exit selection mode" size="sm" onclick={onClose} />
		</div>
	</div>
{/if}

<style>
	:global(.sel-actions [data-bar-icon]),
	:global(.sel-actions [data-bar-icon] button) {
		font-size: 0;
	}
	:global(.sel-actions [data-bar-icon] button) {
		gap: 0;
	}
	:global(.sel-actions[data-mode='overflow']) {
		position: absolute;
		right: 0;
		bottom: calc(100% + 0.5rem);
		flex-direction: column;
		align-items: stretch;
		gap: 0.125rem;
		width: max-content;
		min-width: 14rem;
		max-width: calc(100vw - 2rem);
		padding: 0.25rem;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 0.625rem;
		background: rgb(var(--surface-2));
		box-shadow: var(--shadow-floating);
		visibility: hidden;
	}
	:global(.sel-actions[data-mode='overflow'][data-open='true']) {
		visibility: visible;
	}
	:global(.sel-actions[data-mode='overflow'] > button),
	:global(.sel-actions[data-mode='overflow'] > div),
	:global(.sel-actions[data-mode='overflow'] > div > button) {
		width: 100%;
	}
	:global(.sel-actions[data-mode='overflow'] > button),
	:global(.sel-actions[data-mode='overflow'] > div > button) {
		justify-content: flex-start;
	}
</style>
