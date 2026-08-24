<script lang="ts" generics="T extends CollectionLike">
	import { fly, fade } from 'svelte/transition';
	import { quartOut } from 'svelte/easing';
	import Icon from '$lib/components/Icon.svelte';
	import portal from '$lib/actions/portal';
	import CollectionTreePicker from './CollectionTreePicker.svelte';
	import type { CollectionLike } from './types';

	let {
		selectedCount,
		collections,
		blockedTargetIds,
		onClear,
		onMove
	}: {
		selectedCount: number;
		collections: T[];
		blockedTargetIds: Set<string>;
		onClear: () => void;
		// Resolves once the batch has been applied - the caller (the sidebar)
		// owns surfacing per-id failures and clearing the selection.
		onMove: (targetId: string | null) => Promise<void>;
	} = $props();

	let showTargets = $state(false);
	let busy = $state(false);

	function toggleTargets() {
		showTargets = !showTargets;
	}

	async function handleMove(targetId: string | null) {
		busy = true;
		try {
			await onMove(targetId);
		} finally {
			busy = false;
			showTargets = false;
		}
	}
</script>

<svelte:window onclick={() => showTargets && (showTargets = false)} />

<!--
	`use:portal` moves this to <body> so its fixed positioning (and the "Move
	to..." popover nested inside it) escape the sidebar's ancestor stacking
	context instead of fighting it with z-index - see $lib/actions/portal.ts.
-->
<div class="fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50" use:portal>
	<div
		class="bg-surface-1 text-fg rounded-xl shadow-overlay px-2 py-1.5 flex items-center gap-1 border border-line-strong"
		in:fly={{ y: 10, duration: 200, easing: quartOut }}
		out:fade={{ duration: 150 }}
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => e.stopPropagation()}
		role="toolbar"
		tabindex="-1"
	>
		<div class="px-3 py-1.5 whitespace-nowrap font-mono text-2xs uppercase tracking-[0.07em]">
			<span class="text-fg tabular-nums">{selectedCount}</span>
			<span class="text-fg-muted ml-1">selected</span>
		</div>

		<div class="w-px h-6 bg-line-strong"></div>

		<button
			class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
			onclick={onClear}
		>
			Clear
		</button>

		<div class="relative">
			<button
				class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-50"
				disabled={busy}
				onclick={toggleTargets}
				aria-haspopup="menu"
				aria-expanded={showTargets}
			>
				<Icon name="arrow-right" className="w-4 h-4" />
				Move to…
			</button>
			{#if showTargets}
				<div
					class="absolute bottom-full mb-2 right-0 w-56 max-h-72 bg-surface-1 border border-line-strong rounded-lg shadow-overlay flex flex-col overflow-hidden"
					role="menu"
				>
					<CollectionTreePicker
						{collections}
						blockedIds={blockedTargetIds}
						disabled={busy}
						showRoot
						rootLabel="Top level"
						emptyMessage="No valid target folders"
						onSelect={handleMove}
					/>
				</div>
			{/if}
		</div>

		<div class="w-px h-6 bg-line-strong"></div>

		<button
			class="p-2 text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
			onclick={onClear}
			aria-label="Exit selection mode"
		>
			<Icon name="close" className="w-4 h-4" />
		</button>
	</div>
</div>
