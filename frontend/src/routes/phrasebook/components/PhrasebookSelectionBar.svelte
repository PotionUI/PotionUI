<script lang="ts">
	import { fly, fade } from 'svelte/transition';
	import { quartOut } from 'svelte/easing';
	import Icon from '$lib/components/Icon.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import type { PhrasebookBatchOp } from '$lib/types/api';

	let {
		selectedCount,
		totalCount,
		busy = false,
		categories,
		extraOps = [],
		selectedIds = [],
		onRunOp,
		onSelectAll,
		onClear,
		onReplace,
		onSetActive,
		onMove,
		onDelete
	}: {
		selectedCount: number;
		totalCount: number;
		busy?: boolean;
		categories: { id: string; path: string }[];
		extraOps?: PhrasebookBatchOp[];
		selectedIds?: string[];
		onRunOp?: (op: PhrasebookBatchOp) => void;
		onSelectAll: () => void;
		onClear: () => void;
		onReplace: () => void;
		onSetActive: (isActive: boolean) => void;
		onMove: (categoryId: string) => void;
		onDelete: () => void;
	} = $props();

	let moveOpen = $state(false);
	let moveTarget = $state('');
	let moreOpen = $state(false);

	const actionClass =
		'px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed';

	function closeMove() {
		moveOpen = false;
	}

	function closeMenus() {
		moveOpen = false;
		moreOpen = false;
	}

	function pickOp(op: PhrasebookBatchOp) {
		moreOpen = false;
		onRunOp?.(op);
	}

	function confirmMove() {
		if (!moveTarget) return;
		onMove(moveTarget);
		closeMove();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!moveOpen) return;
		if (e.key === 'Escape') {
			e.preventDefault();
			closeMove();
		} else if (e.key === 'Enter' && moveTarget) {
			e.preventDefault();
			confirmMove();
		}
	}
</script>

<svelte:window
	onclick={() => (moveOpen || moreOpen) && closeMenus()}
	onkeydowncapture={handleKeydown}
/>

{#if selectedCount > 0}
	<div class="fixed bottom-20 md:bottom-6 left-1/2 -translate-x-1/2 z-50" data-selection-bar>
		<div
			class="bg-surface-1 text-fg rounded-xl shadow-overlay px-2 py-1.5 flex items-center gap-1 border border-line-strong"
			in:fly={{ y: 10, duration: 200, easing: quartOut }}
			out:fade={{ duration: 150 }}
			onclick={(e) => e.stopPropagation()}
			onkeydown={(e) => e.stopPropagation()}
			role="toolbar"
			aria-label="Selected values"
			tabindex="-1"
		>
			<div class="px-3 py-1.5 whitespace-nowrap font-mono text-2xs uppercase tracking-[0.07em]">
				<span class="text-fg tabular-nums">{selectedCount}</span>
				<span class="text-fg-muted ml-1">selected</span>
			</div>

			<div class="w-px h-6 bg-line-strong"></div>

			<div class="flex items-center gap-1 px-1">
				{#if selectedCount < totalCount}
					<button type="button" class={actionClass} onclick={onSelectAll}>Select All</button>
				{/if}
				<button type="button" class={actionClass} onclick={onClear}>Clear</button>
			</div>

			<div class="w-px h-6 bg-line-strong"></div>

			<div class="flex items-center gap-1 px-1">
				<Button variant="secondary" size="sm" icon="edit" disabled={busy} onclick={onReplace}>Replace…</Button>

				<button type="button" class={actionClass} disabled={busy} onclick={() => onSetActive(true)}>
					<Icon name="check" className="w-4 h-4" />
					Activate
				</button>

				<button type="button" class={actionClass} disabled={busy} onclick={() => onSetActive(false)}>
					<Icon name="eye-off" className="w-4 h-4" />
					Deactivate
				</button>

				<div class="relative">
					<button
						type="button"
						class={actionClass}
						disabled={busy}
						aria-haspopup="menu"
						aria-expanded={moveOpen}
						onclick={() => {
							moreOpen = false;
							moveOpen = !moveOpen;
						}}
					>
						<Icon name="folder-open" className="w-4 h-4" />
						Move to…
						<Icon name="chevron-up" className="w-3 h-3" />
					</button>
					{#if moveOpen}
						<div
							class="absolute bottom-full mb-2 left-0 w-72 bg-surface-1 border border-line-strong rounded-lg shadow-overlay p-3 flex flex-col gap-3"
							role="menu"
							data-move-menu
						>
							<label class="label" for="phrasebook-move-target">Target category</label>
							<select
								id="phrasebook-move-target"
								class="input text-sm font-mono"
								bind:value={moveTarget}
							>
								<option value="">Choose a category…</option>
								{#each categories as category (category.id)}
									<option value={category.id}>{category.path}</option>
								{/each}
							</select>
							<div class="flex items-center justify-end gap-2">
								<Button variant="secondary" size="sm" onclick={closeMove}>
									<span class="inline-flex items-center gap-1.5">
										Cancel
										<Kbd keys="Esc" />
									</span>
								</Button>
								<Button variant="primary" size="sm" disabled={!moveTarget} onclick={confirmMove}>
									<span class="inline-flex items-center gap-1.5">
										Move {selectedCount}
										<Kbd keys="Enter" />
									</span>
								</Button>
							</div>
						</div>
					{/if}
				</div>

				<button
					type="button"
					class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors flex items-center gap-2 font-medium disabled:opacity-40 disabled:cursor-not-allowed"
					disabled={busy}
					onclick={onDelete}
				>
					<Icon name="trash" className="w-4 h-4" />
					Delete
				</button>

				<PluginSlot hookName="phrasebook.selection.actions" context={{ selectedIds }} />

				{#if extraOps.length > 0}
					<div class="relative">
						<button
							type="button"
							class={actionClass}
							disabled={busy}
							aria-haspopup="menu"
							aria-expanded={moreOpen}
							aria-label="More batch tools"
							data-batch-more
							onclick={() => {
								moveOpen = false;
								moreOpen = !moreOpen;
							}}
						>
							<Icon name="more" className="w-4 h-4" />
							More
							<Icon name="chevron-up" className="w-3 h-3" />
						</button>
						{#if moreOpen}
							<div
								class="absolute bottom-full mb-2 right-0 min-w-[14rem] bg-surface-1 border border-line-strong rounded-lg shadow-overlay p-1 flex flex-col"
								role="menu"
								data-more-menu
							>
								{#each extraOps as op (op.id)}
									<button
										type="button"
										role="menuitem"
										class="px-3 py-1.5 text-sm text-left text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
										data-batch-op={op.id}
										onclick={() => pickOp(op)}
									>
										{op.label}
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>

			<div class="w-px h-6 bg-line-strong"></div>

			<button
				type="button"
				class="p-2 text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
				onclick={onClear}
				aria-label="Clear selection"
			>
				<Icon name="close" className="w-4 h-4" />
			</button>
		</div>
	</div>
{/if}
