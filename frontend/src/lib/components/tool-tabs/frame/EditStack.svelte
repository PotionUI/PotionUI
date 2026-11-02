<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton, Menu } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import {
		DEFAULT_PINNED_LANES,
		appliedCount,
		canMove,
		canRedo,
		canUndo,
		redoLabel,
		stackRows,
		undoLabel,
		undoneCount,
		type EditStackState,
		type LanePair,
		type StackRow
	} from '$lib/tool-tabs/frame/editStack';
	import type { StackVariant } from '$lib/tool-tabs/frame/types';

	let {
		stack,
		original,
		variant = 'panel',
		pinned = DEFAULT_PINNED_LANES,
		moreItems = [],
		notes,
		onreopen,
		onseek,
		ontogglehidden,
		onremove,
		onmove,
		onundo,
		onredo,
		onmore
	}: {
		stack: EditStackState<unknown>;
		original: { label?: string; summary: string; thumb?: string | null };
		variant?: StackVariant;
		pinned?: readonly LanePair[];
		moreItems?: MenuItem[];
		notes?: Snippet;
		onreopen: (id: string) => void;
		onseek: (cursor: number) => void;
		ontogglehidden: (id: string) => void;
		onremove: (id: string) => void;
		onmove: (id: string, to: number) => void;
		onundo?: () => void;
		onredo?: () => void;
		onmore?: (id: string) => void;
	} = $props();

	const ROW_HEIGHT = 44;

	let rows = $derived(stackRows(stack));
	let applied = $derived(appliedCount(stack));
	let undone = $derived(undoneCount(stack));
	let sheet = $derived(variant === 'sheet');
	let drag = $state<{ id: string; from: number; startY: number; dy: number; height: number } | null>(null);

	let dropIndex = $derived.by(() => {
		if (!drag) return -1;
		const shift = Math.round(drag.dy / drag.height);
		return Math.max(0, Math.min(applied - 1, drag.from + shift));
	});
	let dropAllowed = $derived(drag ? dropIndex === drag.from || canMove(stack, drag.id, dropIndex, pinned) : false);

	const pad = (n: number) => String(n).padStart(2, '0');

	function draggable(row: StackRow<unknown>): boolean {
		return !!row.step && (row.status === 'applied' || row.status === 'current') && !stack.pending?.target;
	}

	function rowLabel(row: StackRow<unknown>): string {
		if (row.status === 'undone') return `Redo to ${row.step?.label}`;
		if (row.status === 'pending') return `${row.pending?.label}, pending`;
		return `Reopen ${row.step?.label}${row.hidden ? ', hidden' : ''}`;
	}

	function activate(row: StackRow<unknown>) {
		if (!row.step) return;
		if (row.status === 'undone') onseek(row.index);
		else if (row.status === 'applied' || row.status === 'current') onreopen(row.step.id);
	}

	function onRowKeydown(event: KeyboardEvent, row: StackRow<unknown>) {
		if (!event.altKey || !row.step || !draggable(row)) return;
		if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
		event.preventDefault();
		const to = row.index - 1 + (event.key === 'ArrowUp' ? -1 : 1);
		if (canMove(stack, row.step.id, to, pinned)) onmove(row.step.id, to);
	}

	function startDrag(event: PointerEvent, row: StackRow<unknown>) {
		if (!row.step || !draggable(row) || event.button !== 0) return;
		const handle = event.currentTarget as HTMLElement;
		handle.setPointerCapture?.(event.pointerId);
		const height = handle.closest<HTMLElement>('[data-stack-row]')?.offsetHeight || ROW_HEIGHT;
		drag = { id: row.step.id, from: row.index - 1, startY: event.clientY, dy: 0, height };
		event.preventDefault();
	}

	function moveDrag(event: PointerEvent) {
		if (drag) drag.dy = event.clientY - drag.startY;
	}

	function endDrag() {
		if (!drag) return;
		const { id, from } = drag;
		const to = dropIndex;
		const ok = dropAllowed;
		drag = null;
		if (ok && to !== from) onmove(id, to);
	}
</script>

<section class="flex min-h-0 flex-col {sheet ? '' : 'stack-panel h-[236px] flex-none border-t border-line'}" aria-label="Edits" data-edit-stack={variant}>
	{#if sheet}
		<div class="flex flex-none items-center gap-2 px-2 pb-2.5 pt-1">
			<h2 class="text-lg font-semibold text-fg">Edits</h2>
			<span class="font-mono text-sm tabular-nums text-fg-subtle" data-stack-count>{applied}</span>
			<span class="flex-1"></span>
			<IconButton icon="editor-undo" label={undoLabel(stack) ? `Undo ${undoLabel(stack)}` : 'Undo'} size="sm" disabled={!canUndo(stack)} onclick={() => onundo?.()} />
			<IconButton icon="editor-redo" label={redoLabel(stack) ? `Redo ${redoLabel(stack)}` : 'Redo'} size="sm" disabled={!canRedo(stack)} onclick={() => onredo?.()} />
		</div>
	{:else}
		<div class="flex h-11 flex-none items-center gap-2 pl-4 pr-2">
			<Icon name="editor-layers" className="h-3.5 w-3.5 flex-none text-fg-muted" />
			<h2 class="text-base font-semibold text-fg">Edits</h2>
			<span class="font-mono text-sm tabular-nums text-fg-subtle" data-stack-count>{applied}{undone ? ` + ${undone} undone` : ''}</span>
			<span class="flex-1"></span>
			{#if moreItems.length}
				<Menu label="Edit stack options" items={moreItems} onselect={(id) => onmore?.(id)} />
			{/if}
		</div>
	{/if}

	<div class="min-h-0 flex-1 {sheet ? '' : 'overflow-y-auto px-2 pb-2'}" role="list" data-stack-rows>
		{#each rows as row (row.key)}
			{@const step = row.step ?? row.pending}
			{@const dragging = drag?.id === row.key}
			<div
				role="listitem"
				class="group relative flex h-11 items-center gap-2 rounded pl-1 pr-1.5
					{row.status === 'current' ? 'is-current bg-signal/[0.08]' : 'hover:bg-surface-2'}
					{row.status === 'pending' ? 'border border-dashed border-line-hover' : ''}
					{row.status === 'undone' ? 'opacity-[0.45]' : ''}
					{dragging ? `z-10 bg-surface-2 shadow-floating ${dropAllowed ? '' : 'cursor-not-allowed'}` : ''}"
				style={dragging && drag ? `transform: translateY(${drag.dy}px)` : undefined}
				data-stack-row={row.key}
				data-status={row.status}
				data-hidden={row.hidden || undefined}
				data-drop-blocked={dragging && !dropAllowed ? '' : undefined}
			>
				<span
					class="grid w-3 flex-none place-items-center text-fg-disabled {draggable(row) ? 'cursor-grab touch-none' : ''}"
					aria-hidden="true"
					onpointerdown={(event) => startDrag(event, row)}
					onpointermove={moveDrag}
					onpointerup={endDrag}
					onpointercancel={() => (drag = null)}
					data-stack-grip={draggable(row) ? row.key : undefined}
				>
					{#if row.step}<Icon name="grip" className="h-3.5 w-3.5" />{/if}
				</span>
				{#snippet body()}
					<span class="w-[18px] flex-none font-mono text-sm tabular-nums text-fg-subtle">{pad(row.index)}</span>
					{#if row.key === 'original'}
						<span
							class="grid h-7 w-7 flex-none place-items-center rounded bg-surface-2 bg-cover bg-center text-fg-muted"
							style={original.thumb ? `background-image: url(${JSON.stringify(original.thumb)})` : undefined}
						>
							{#if !original.thumb}<Icon name="image" className="h-3.5 w-3.5" />{/if}
						</span>
					{:else}
						<span class="grid h-7 w-7 flex-none place-items-center rounded bg-surface-2 text-fg-muted {row.hidden ? 'opacity-[0.45]' : ''}">
							<Icon name={step?.icon ?? 'sliders'} className="h-3.5 w-3.5" />
						</span>
					{/if}
					<span class="flex min-w-0 flex-1 flex-col">
						<span
							class="truncate text-base font-medium leading-[18px]
								{row.status === 'pending' ? 'text-fg-muted' : 'text-fg'}
								{row.status === 'undone' ? 'line-through decoration-fg-subtle' : ''}
								{row.hidden ? 'opacity-[0.45]' : ''}"
						>
							{row.key === 'original' ? (original.label ?? 'Original') : step?.label}
						</span>
						<span class="truncate font-mono text-sm text-fg-subtle">
							{row.key === 'original' ? original.summary : row.status === 'undone' ? 'Undone' : step?.summary}
						</span>
					</span>
				{/snippet}
				{#if row.key === 'original' || row.status === 'pending'}
					<span class="flex min-w-0 flex-1 items-center gap-2">{@render body()}</span>
				{:else}
					<button
						type="button"
						class="flex min-w-0 flex-1 items-center gap-2 rounded text-left focus-visible:outline-none"
						aria-label={rowLabel(row)}
						onclick={() => activate(row)}
						onkeydown={(event) => onRowKeydown(event, row)}
						data-stack-open={row.key}
					>
						{@render body()}
					</button>
				{/if}
				{#if row.status === 'pending'}
					<span class="inline-flex h-5 flex-none items-center rounded bg-surface-3 px-1.5 font-mono text-sm text-fg-muted">PENDING</span>
				{:else if row.step && !sheet && row.status !== 'undone'}
					<span class="flex flex-none {row.hidden ? '' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}" data-stack-actions>
						<Tooltip text={row.hidden ? 'Show step' : 'Hide step'} position="top">
							<IconButton
								icon={row.hidden ? 'editor-eye-off' : 'editor-eye'}
								label="{row.hidden ? 'Show' : 'Hide'} {row.step.label}"
								size="xs"
								ariaPressed={row.hidden}
								onclick={() => row.step && ontogglehidden(row.step.id)}
								data-stack-hide={row.key}
							/>
						</Tooltip>
						<Tooltip text="Remove step" position="top">
							<IconButton
								icon="trash"
								label="Remove {row.step.label}"
								size="xs"
								onclick={() => row.step && onremove(row.step.id)}
								data-stack-remove={row.key}
							/>
						</Tooltip>
					</span>
				{/if}
			</div>
		{/each}
	</div>

	{#if undone && !stack.pending}
		<div class="mx-2 mb-2.5 mt-1 flex flex-none items-start gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" data-stack-note="undone">
			<Icon name="info" className="mt-px h-3.5 w-3.5 flex-none" />
			<span>Showing step {pad(applied)}. Redo brings {redoLabel(stack)} back; a new edit drops it.</span>
		</div>
	{/if}
	{#if notes}
		{@render notes()}
	{/if}
</section>

<style>
	.is-current::before {
		content: '';
		position: absolute;
		left: -8px;
		top: 8px;
		bottom: 8px;
		width: 2px;
		background: rgb(var(--signal));
	}

	:global(aside:has([data-options-panel][data-collapsed])) .stack-panel {
		flex: 1 1 0%;
		height: auto;
	}
</style>
