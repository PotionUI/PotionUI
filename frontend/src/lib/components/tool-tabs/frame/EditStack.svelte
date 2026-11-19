<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button, IconButton, Menu, Spinner } from '$lib/components/ui';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import type { StepRowInfo } from '$lib/editors/stepKinds';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import {
		DEFAULT_PINNED_LANES,
		appliedCount,
		canMove,
		canRedo,
		canUndo,
		opacityOf,
		redoLabel,
		stackRows,
		undoLabel,
		undoneCount,
		type EditStackState,
		type EditStep,
		type LanePair,
		type StackRow
	} from '$lib/tool-tabs/frame/editStack';
	import { readStackFolds, writeStackFolds, type StackFoldName } from '$lib/tool-tabs/frame/stackFolds';
	import type { LiveLayer, StackProgress, StackVariant } from '$lib/tool-tabs/frame/types';
	import type { PatchMask } from '$lib/tool-tabs/image/layers';

	let {
		stack,
		original,
		variant = 'panel',
		layered = false,
		selectedId = null,
		isLayer = () => false,
		layerThumb = () => null,
		movable = true,
		onopacity,
		maskOf = () => null,
		showMask = false,
		ontogglemask,
		headerActions,
		duplicable = () => false,
		mergeable = () => false,
		onduplicate,
		onmerge,
		onrename,
		folded = false,
		collapsed = $bindable(false),
		pinned = DEFAULT_PINNED_LANES,
		moreItems = [],
		notes,
		progress = null,
		rowInfo,
		onrowaction,
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
		layered?: boolean;
		selectedId?: string | null;
		isLayer?: (step: EditStep<unknown>) => boolean;
		layerThumb?: (step: EditStep<unknown>) => string | null;
		movable?: boolean;
		onopacity?: (id: string, opacity: number, live: boolean) => void;
		maskOf?: (step: EditStep<unknown>) => PatchMask | null;
		showMask?: boolean;
		ontogglemask?: () => void;
		headerActions?: Snippet;
		duplicable?: (step: EditStep<unknown>) => boolean;
		mergeable?: (upper: EditStep<unknown>, lower: EditStep<unknown> | null) => boolean;
		onduplicate?: (id: string) => void;
		onmerge?: (id: string) => void;
		onrename?: (id: string, label: string) => void;
		folded?: boolean;
		collapsed?: boolean;
		pinned?: readonly LanePair[];
		moreItems?: MenuItem[];
		notes?: Snippet;
		progress?: StackProgress | null;
		rowInfo?: (step: EditStep<unknown>) => StepRowInfo | null;
		onrowaction?: (id: string, action: string) => void;
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
	let shownRows = $derived(layered ? [...rows.slice(1).reverse(), rows[0]] : rows);
	let layerRows = $derived(layered ? shownRows.filter((row) => row.key === 'original' || isLayerKind(row)) : []);
	let editRows = $derived(layered ? shownRows.filter((row) => row.key !== 'original' && !isLayerKind(row)) : []);
	let editNumbers = $derived(new Map(rows.filter((row) => row.key !== 'original' && !isLayerKind(row)).map((row, i) => [row.key, i + 1] as const)));
	let layerCount = $derived(rows.filter((row) => row.key !== 'original' && row.status !== 'undone' && row.status !== 'pending' && isLayerKind(row)).length);
	let editCount = $derived(rows.filter((row) => row.key !== 'original' && row.status !== 'undone' && row.status !== 'pending' && !isLayerKind(row)).length);
	let dragOpacity = $state<number | null>(null);
	let applied = $derived(appliedCount(stack));
	let undone = $derived(undoneCount(stack));
	let sheet = $derived(variant === 'sheet');
	let liveLayer = $derived<LiveLayer | null>(layered && progress && !progress.target ? (progress.live ?? null) : null);
	let liveAnchor = $derived.by(() => {
		if (!liveLayer) return null;
		if (progress?.after && layerRows.some((row) => row.key === progress.after)) return progress.after;
		let last = 'original';
		for (const row of layerRows.slice().reverse()) if (row.status !== 'undone' && row.status !== 'pending') last = row.key;
		return last;
	});
	let opened = $state(false);
	let shut = $derived(!sheet && (collapsed || (folded && !opened)));
	let folds = $state(readStackFolds());
	let bothFolded = $derived(layered && !sheet && folds.layers && folds.edits);

	function toggleFold(name: StackFoldName) {
		if (shut) {
			if (folds[name]) {
				folds = { ...folds, [name]: false };
				writeStackFolds(folds);
			}
			if (folded && !collapsed) opened = true;
			else collapsed = false;
			return;
		}
		folds = { ...folds, [name]: !folds[name] };
		writeStackFolds(folds);
	}

	$effect(() => {
		if (!folded) opened = false;
	});
	let drag = $state<{ id: string; from: number; startY: number; dy: number; height: number } | null>(null);

	let dropIndex = $derived.by(() => {
		if (!drag) return -1;
		const shift = Math.round(drag.dy / drag.height) * (layered ? -1 : 1);
		if (layered) {
			const group = sectionIndices(drag.id);
			const at = Math.max(0, Math.min(group.length - 1, group.indexOf(drag.from) + shift));
			return group[at] ?? drag.from;
		}
		return Math.max(0, Math.min(applied - 1, drag.from + shift));
	});
	let dropAllowed = $derived(drag ? dropIndex === drag.from || canMove(stack, drag.id, dropIndex, pinned) : false);

	let renaming = $state<{ id: string } | null>(null);
	let renameText = $state('');

	function finishRename(commit: boolean) {
		const current = renaming;
		const text = renameText.trim();
		renaming = null;
		if (commit && current && text) onrename?.(current.id, text);
	}

	function focusInput(node: HTMLInputElement) {
		setTimeout(() => {
			node.focus();
			node.select();
		}, 0);
	}

	let refusal = $derived.by(() => {
		if (!drag || dropAllowed || layered) return null;
		const moving = stack.steps.find((step) => step.id === drag!.id);
		const path = stack.steps.slice(Math.min(drag.from, dropIndex), Math.max(drag.from, dropIndex) + 1).filter((step) => step.id !== drag!.id);
		const blocker = path.find((step) => step.lane && pinned.some((pair) => pair.includes(step.lane!) && (!moving?.lane || moving.lane !== step.lane) && pair.includes(moving?.lane ?? '')));
		const below = dropIndex < drag.from;
		if (!moving || !blocker) return `${moving?.label ?? 'This layer'} can't go there.`;
		return `${moving.label} can't go ${below ? 'below' : 'above'} ${blocker.label}. ${blocker.label} reshapes everything beneath it, so layers stay on one side.`;
	});

	const pad = (n: number) => String(n).padStart(2, '0');

	function draggable(row: StackRow<unknown>): boolean {
		return movable && !!row.step && (row.status === 'applied' || row.status === 'current') && !stack.pending?.target;
	}

	function isLayerKind(row: StackRow<unknown>): boolean {
		const step = row.step ?? row.pending;
		return !!step && isLayer(step as EditStep<unknown>);
	}

	function sectionIndices(id: string): number[] {
		const steps = stack.steps.slice(0, applied);
		const moving = steps.find((step) => step.id === id);
		if (!moving) return [];
		const layer = isLayer(moving);
		return steps.flatMap((step, index) => (isLayer(step) === layer ? [index] : []));
	}

	function neighbour(row: StackRow<unknown>, offset: number): number | null {
		if (!row.step) return null;
		if (!layered) return row.index - 1 + offset;
		const group = sectionIndices(row.step.id);
		const at = group.indexOf(row.index - 1) + offset;
		return at >= 0 && at < group.length ? group[at] : null;
	}

	function canStep(row: StackRow<unknown>, offset: number): boolean {
		const to = neighbour(row, offset);
		return movable && !!row.step && to !== null && canMove(stack, row.step.id, to, pinned);
	}

	function rowLabel(row: StackRow<unknown>): string {
		if (row.status === 'undone') return `Redo to ${row.step?.label}`;
		if (row.status === 'pending') return `${row.pending?.label}, not applied`;
		if (row.status === 'editing') return `${row.step?.label}, being edited`;
		if (row.status === 'selected') return `${row.step?.label}, selected`;
		return `Reopen ${row.step?.label}${row.hidden ? ', hidden' : ''}`;
	}

	function isSelected(row: StackRow<unknown>): boolean {
		return row.reopened || (!!row.step && row.key === selectedId && row.status !== 'undone');
	}

	function isLayerRow(row: StackRow<unknown>): boolean {
		return layered && !!row.step && isLayer(row.step);
	}

	function rowHeight(row: StackRow<unknown>): string {
		if (sheet && layered) return 'h-12';
		if (!layered || row.key === 'original' || isLayerRow(row)) return 'h-11';
		return 'h-10';
	}

	function percent(step: EditStep<unknown>): number {
		return Math.round(opacityOf(step) * 100);
	}

	function drawerItems(row: StackRow<unknown>): MenuItem[] {
		return [
			{ id: 'up', label: 'Move up', icon: 'chevron-up', disabled: !canStep(row, 1) },
			{ id: 'down', label: 'Move down', icon: 'chevron-down', disabled: !canStep(row, -1) },
			{ id: 'sep', label: '', separator: true },
			{ id: 'rename', label: 'Rename', icon: 'pencil', disabled: !onrename || !row.step || row.status === 'undone' },
			{ id: 'duplicate', label: 'Duplicate', icon: 'copy', disabled: !row.step || !duplicable(row.step) || row.status === 'undone' },
			{ id: 'merge', label: 'Merge down', icon: 'layers', disabled: !row.step || !mergeable(row.step, stack.steps[row.index - 2] ?? null) },
			{ id: 'sep2', label: '', separator: true },
			{ id: 'delete', label: 'Delete', icon: 'trash', danger: true }
		];
	}

	function chooseDrawer(row: StackRow<unknown>, id: string) {
		if (!row.step) return;
		const up = neighbour(row, 1);
		const down = neighbour(row, -1);
		if (id === 'up' && up !== null && canStep(row, 1)) onmove(row.step.id, up);
		else if (id === 'down' && down !== null && canStep(row, -1)) onmove(row.step.id, down);
		else if (id === 'rename') {
			renameText = row.step.label;
			renaming = { id: row.step.id };
		}
		else if (id === 'duplicate') onduplicate?.(row.step.id);
		else if (id === 'merge') onmerge?.(row.step.id);
		else if (id === 'delete') onremove(row.step.id);
	}

	function slideOpacity(row: StackRow<unknown>, value: number) {
		if (!row.step) return;
		dragOpacity = value / 100;
		onopacity?.(row.step.id, value / 100, true);
	}

	function commitOpacity(row: StackRow<unknown>) {
		if (!row.step) return;
		const value = dragOpacity ?? opacityOf(row.step);
		dragOpacity = null;
		onopacity?.(row.step.id, value, false);
	}

	function activate(row: StackRow<unknown>) {
		if (!row.step) return;
		if (row.status === 'undone') onseek(row.index);
		else if (row.status === 'applied' || row.status === 'current' || row.status === 'selected' || row.status === 'editing') onreopen(row.step.id);
	}

	function onRowKeydown(event: KeyboardEvent, row: StackRow<unknown>) {
		if (!event.altKey || !row.step || !draggable(row)) return;
		if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
		event.preventDefault();
		const offset = (event.key === 'ArrowUp') === layered ? 1 : -1;
		const to = neighbour(row, offset);
		if (to !== null && canStep(row, offset)) onmove(row.step.id, to);
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

{#snippet refusalLine()}
	<div class="mx-1 my-0.5" data-stack-refuse>
		<div class="border-t-2 border-dashed border-danger" data-stack-dropline="blocked"></div>
		<p class="flex items-start gap-1.5 px-1 pt-1 text-sm text-danger"><Icon name="lock" className="mt-px h-3.5 w-3.5 flex-none" /><span>{refusal}</span></p>
	</div>
{/snippet}

{#snippet stackRow(row: StackRow<unknown>)}
		{@const step = row.step ?? row.pending}
		{@const dragging = drag?.id === row.key}
		{@const info = row.step && rowInfo ? rowInfo(row.step) : null}
		{@const layerRow = isLayerRow(row)}
		{@const thumb = info?.thumb ?? (layerRow && row.step ? layerThumb(row.step) : null)}
		{@const slim = layered && row.key !== 'original' && !layerRow}
		{@const dropHere = !!refusal && !!drag && row.index - 1 === dropIndex}
		{#if dropHere && (dropIndex > (drag?.from ?? 0)) === layered}
			{@render refusalLine()}
		{/if}
		<div
			role="listitem"
			class="group relative flex {rowHeight(row)} items-center gap-2 rounded pl-1 pr-1.5
				{row.status === 'current' || row.reopened ? 'is-current bg-signal/[0.08]' : 'hover:bg-surface-2'}
				{row.status === 'pending' ? 'border border-dashed border-line-hover' : ''}
				{row.status === 'undone' ? 'opacity-[0.45]' : ''}
				{dragging ? `z-10 bg-surface-2 shadow-floating ${dropAllowed ? '' : 'cursor-not-allowed'}` : ''}"
			style={dragging && drag ? `transform: translateY(${drag.dy}px)` : undefined}
			data-stack-row={row.key}
			data-stack-index={row.index}
			data-status={row.status}
			aria-current={row.reopened ? 'true' : undefined}
			data-hidden={row.hidden || undefined}
			data-stack-kind={layered && row.step ? (layerRow ? 'layer' : 'slim') : undefined}
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
				{#if !layered}
					<span class="w-[18px] flex-none font-mono text-sm tabular-nums text-fg-subtle">{pad(row.index)}</span>
				{:else if row.key !== 'original' && !isLayerKind(row)}
					<span class="w-[18px] flex-none font-mono text-sm tabular-nums text-fg-subtle" data-stack-number={row.key}>{pad(editNumbers.get(row.key) ?? 0)}</span>
				{/if}
				{#if row.key === 'original' && layered && original.thumb}
					<span class="alpha-checker flex h-7 flex-none items-center overflow-hidden rounded" data-stack-thumb>
						<img src={original.thumb} alt="" class="h-7 w-auto max-w-12 object-contain" />
					</span>
				{:else if row.key === 'original'}
					{#if original.thumb}
						<span class="alpha-checker relative h-7 w-7 flex-none overflow-hidden rounded" data-stack-thumb>
							<span class="absolute inset-0 bg-cover bg-center" style={`background-image: url(${JSON.stringify(original.thumb)})`}></span>
						</span>
					{:else}
						<span class="grid h-7 w-7 flex-none place-items-center rounded bg-surface-2 text-fg-muted">
							<Icon name="image" className="h-3.5 w-3.5" />
						</span>
					{/if}
				{:else if thumb && layered}
					<span class="alpha-checker flex h-7 flex-none items-center overflow-hidden rounded {row.hidden ? 'opacity-[0.45]' : ''}" data-stack-thumb>
						<img src={thumb} alt="" class="h-7 w-auto max-w-12 object-contain" />
					</span>
				{:else if thumb}
					<span class="alpha-checker relative h-7 w-7 flex-none overflow-hidden rounded {row.hidden ? 'opacity-[0.45]' : ''}" data-stack-thumb>
						<span class="absolute inset-0 bg-cover bg-center" style={`background-image: url(${JSON.stringify(thumb)})`}></span>
					</span>
				{:else}
					<span class="grid {slim ? 'h-6 w-6' : 'h-7 w-7'} flex-none place-items-center rounded bg-surface-2 text-fg-muted {row.hidden ? 'opacity-[0.45]' : ''}">
						<Icon name={step?.icon ?? 'sliders'} className="h-3.5 w-3.5" />
					</span>
				{/if}
				<span class="flex min-w-0 flex-1 flex-col">
					<span
						class="flex min-w-0 items-center gap-1.5 text-base font-medium leading-[18px]
							{row.status === 'pending' ? 'text-fg-muted' : 'text-fg'}
							{row.status === 'undone' ? 'line-through decoration-fg-subtle' : ''}
							{row.hidden ? 'opacity-[0.45]' : ''}"
					>
						{#if row.status === 'editing' || row.status === 'pending'}<i class="h-1.5 w-1.5 flex-none rounded-full bg-signal" data-stack-marker={row.status}></i>{/if}
						{#if renaming && row.step && renaming.id === row.step.id}
							<input
								use:focusInput
								type="text"
								aria-label="Layer name"
								class="h-6 min-w-0 flex-1 rounded border border-line-strong bg-surface-1 px-1.5 text-base text-fg focus-visible:outline-none focus-visible:border-signal"
								bind:value={renameText}
								onclick={(event) => event.stopPropagation()}
								onkeydown={(event) => {
									event.stopPropagation();
									if (event.key === 'Enter') finishRename(true);
									else if (event.key === 'Escape') finishRename(false);
								}}
								onblur={() => finishRename(true)}
								data-stack-rename={row.key}
							/>
						{:else}
							<span class="truncate">{row.key === 'original' ? (original.label ?? 'Original') : step?.label}</span>
						{/if}
					</span>
					{#if info && row.step && row.status !== 'undone' && row.status !== 'pending' && info.state.kind !== 'done'}
						<span class="flex min-w-0 items-center gap-1.5 text-sm" data-stack-state={info.state.kind}>
							{#if info.state.kind === 'busy'}
								{#if info.state.progress !== undefined}
									<span class="truncate text-signal">{info.state.label} <span class="font-mono tabular-nums">{Math.round(info.state.progress)}%</span></span>
								{:else}
									<Spinner size="sm" /><span class="truncate text-fg-muted">{info.state.label}</span>
								{/if}
							{:else if info.state.kind === 'skipped'}
								{#if info.state.detail}
									<Tooltip text={info.state.detail} position="top" wrapperClass="flex min-w-0">
										<span class="truncate {info.state.tone === 'danger' ? 'text-danger' : info.state.tone === 'warning' ? 'text-warning' : 'text-fg-subtle'}">{info.state.label}</span>
									</Tooltip>
								{:else}
									<span class="truncate {info.state.tone === 'danger' ? 'text-danger' : info.state.tone === 'warning' ? 'text-warning' : 'text-fg-subtle'}">{info.state.label}</span>
								{/if}
							{/if}
						</span>
					{:else}
						<span class="truncate font-mono text-sm text-fg-subtle">
							{#if row.status === 'editing'}<span class="font-sans text-signal" data-stack-editing>Editing</span><span class="px-1">·</span>{:else if row.status === 'pending'}<span class="font-sans" data-stack-editing>Not applied</span><span class="px-1">·</span>{/if}{row.key === 'original' ? original.summary : row.status === 'undone' ? 'Undone' : (step?.summary ?? '')}
						</span>
					{/if}
				</span>
			{/snippet}
			{#if row.key === 'original' || row.status === 'pending' || (info?.locked && row.status !== 'undone') || (renaming && renaming.id === row.step?.id)}
				<span class="flex min-w-0 flex-1 items-center gap-2" data-stack-locked={info?.locked ? '' : undefined}>{@render body()}</span>
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
			{#if info && row.step && row.status !== 'pending' && row.status !== 'undone'}
				<span class="flex min-w-0 flex-none items-center gap-1.5">
					{#if info.row && info.entry}
						<EditorEntryComponent entry={info.entry} componentRef={info.row} props={{ step: row.step, state: info.state }} variant="row" />
					{/if}
					{#if info.state.kind === 'skipped' && info.state.action}
						{@const action = info.state.action}
						<Button size="xs" variant="ghost" onclick={() => row.step && onrowaction?.(row.step.id, action.id)}>{action.label}</Button>
					{/if}
				</span>
			{/if}
			{#if info?.state.kind === 'busy' && info.state.progress !== undefined}
				<span class="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 bg-surface-3" data-stack-progress><i class="block h-full bg-signal" style="width: {info.state.progress}%"></i></span>
			{/if}
			{#if layered && row.step && row.status !== 'undone' && row.status !== 'pending'}
				{#if layerRow && !sheet}
					<span class="flex-none font-mono text-sm tabular-nums {isSelected(row) ? 'text-fg' : 'text-fg-subtle'} {row.hidden ? 'opacity-[0.45]' : ''}" data-stack-opacity={row.key}>{percent(row.step)}%</span>
				{/if}
				{#if info?.hideToggle !== false}
					<IconButton
						icon={row.hidden ? 'editor-eye-off' : 'editor-eye'}
						label="{row.hidden ? 'Show' : 'Hide'} {row.step.label}"
						size="xs"
						class={sheet ? 'min-h-11 min-w-11' : ''}
						ariaPressed={row.hidden}
						onclick={() => row.step && ontogglehidden(row.step.id)}
						data-stack-hide={row.key}
					/>
				{/if}
			{/if}
			{#if row.step && !sheet && row.status !== 'undone'}
				<span class="flex flex-none {row.hidden || row.reopened ? '' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}" data-stack-actions>
					{#each info?.actions ?? [] as action (action.id)}
						<Tooltip text={action.label} position="top">
							<IconButton icon={action.icon} label="{action.label} {row.step.label}" size="xs" onclick={() => row.step && onrowaction?.(row.step.id, action.id)} data-stack-action={action.id} />
						</Tooltip>
					{/each}
					{#if info?.hideToggle !== false && !layered}
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
					{/if}
					{#if info?.removable !== false}
				<Tooltip text="Remove step" position="top">
						<IconButton
							icon="trash"
							label="Remove {row.step.label}"
							size="xs"
							onclick={() => row.step && onremove(row.step.id)}
							data-stack-remove={row.key}
						/>
					</Tooltip>
				{/if}
				</span>
			{/if}
		</div>
		{#if layerRow && isSelected(row) && row.step}
			{@const patchMask = maskOf(row.step)}
			<div class="mb-1 ml-1 flex flex-col gap-1.5 border-l-2 border-signal bg-signal/[0.04] py-1 pl-3 pr-1.5" role="group" aria-label="Layer opacity" data-layer-drawer={row.key}>
				<div class="flex items-center gap-1">
				<div class="min-w-0 flex-1">
					<SliderField
						name="layer-opacity"
						config={{ title: 'Opacity', min: 0, max: 100, default: 100, unit: '%', layout: 'inline', reset: true, legend: false }}
						value={percent(row.step)}
						onChange={(_name, value) => slideOpacity(row, Number(value))}
						onCommit={() => commitOpacity(row)}
					/>
				</div>
				<Menu label="Layer options for {row.step.label}" icon="chat-more" size="xs" items={drawerItems(row)} onselect={(id) => chooseDrawer(row, id)} menuAttrs={{ 'data-layer-menu': row.key }} />
				</div>
				{#if patchMask}
					<div class="flex items-center gap-2.5 pb-0.5" data-patch-mask={row.key}>
						<span class="w-12 flex-none text-sm text-fg-subtle">Mask</span>
						<span class="relative h-7 w-[42px] flex-none overflow-hidden rounded bg-canvas shadow-[inset_0_0_0_1px_rgb(var(--line-strong))]" data-patch-mask-thumb>
							{#if patchMask.url}
								<img src={patchMask.url} alt="" class="h-full w-full object-contain" />
							{:else}
								<span class="absolute bg-fg/90" style="left: {patchMask.rect.x * 100}%; top: {patchMask.rect.y * 100}%; width: {patchMask.rect.width * 100}%; height: {patchMask.rect.height * 100}%"></span>
							{/if}
						</span>
						<span class="min-w-0 flex-1 text-sm leading-tight text-fg">Edited region<span class="block text-fg-subtle">Outside it, unchanged</span></span>
						<Button size="xs" pressed={showMask} onclick={() => ontogglemask?.()} attrs={{ 'data-patch-mask-toggle': row.key }}>{showMask ? 'Hide mask' : 'Show mask'}</Button>
					</div>
				{/if}
			</div>
		{/if}
		{#if dropHere && (dropIndex > (drag?.from ?? 0)) !== layered}
			{@render refusalLine()}
		{/if}
{/snippet}

{#snippet liveRowView(live: LiveLayer)}
	<div
		role="listitem"
		class="group relative flex {sheet ? 'h-12' : 'h-11'} items-center gap-2 rounded bg-signal/[0.08] pl-1 pr-1.5 before:absolute before:-left-2 before:inset-y-2 before:w-0.5 before:bg-signal"
		data-stack-live
		data-status="live"
		aria-current="true"
		data-hidden={live.hidden || undefined}
	>
		<span class="w-3 flex-none" aria-hidden="true"></span>
		<span class="grid h-7 w-7 flex-none place-items-center rounded bg-surface-2 text-fg-muted {live.hidden ? 'opacity-[0.45]' : ''}">
			<Icon name="editor-paint" className="h-3.5 w-3.5" />
		</span>
		<span class="flex min-w-0 flex-1 flex-col {live.hidden ? 'opacity-[0.45]' : ''}">
			<span class="flex min-w-0 items-center gap-1.5 text-base font-medium leading-[18px] text-fg">
				<i class="h-1.5 w-1.5 flex-none rounded-full bg-signal" data-stack-marker="live"></i>
				<span class="truncate" data-stack-live-name>{live.label}</span>
			</span>
			<span class="truncate font-mono text-sm text-fg-subtle"><span class="font-sans text-signal">Painting</span><span class="px-1">·</span>New layer</span>
		</span>
		{#if !sheet}
			<span class="flex-none font-mono text-sm tabular-nums text-fg {live.hidden ? 'opacity-[0.45]' : ''}" data-stack-opacity="live">{Math.round(live.opacity * 100)}%</span>
		{/if}
		<IconButton
			icon={live.hidden ? 'editor-eye-off' : 'editor-eye'}
			label="{live.hidden ? 'Show' : 'Hide'} {live.label}"
			size="xs"
			class={sheet ? 'min-h-11 min-w-11' : ''}
			ariaPressed={live.hidden}
			onclick={() => live.onhide()}
			data-stack-hide="live"
		/>
	</div>
	<div class="mb-1 ml-1 flex flex-col gap-1.5 border-l-2 border-signal bg-signal/[0.04] py-1 pl-3 pr-1.5" role="group" aria-label="Layer opacity" data-layer-drawer="live">
		<SliderField
			name="layer-opacity"
			config={{ title: 'Opacity', min: 0, max: 100, default: 100, unit: '%', layout: 'inline', reset: true, legend: false }}
			value={Math.round(live.opacity * 100)}
			onChange={(_name, value) => live.onopacity(Number(value) / 100)}
		/>
	</div>
{/snippet}

{#snippet sectionHeader(name: StackFoldName)}
	{@const count = name === 'layers' ? layerCount : editCount}
	{@const closed = shut || folds[name]}
	{@const title = name === 'layers' ? 'Layers' : 'Edits'}
	<div class="relative flex flex-none items-center gap-2 {sheet ? 'h-12 px-2' : name === 'layers' ? 'h-11 pl-4 pr-2' : 'h-10 border-t border-line pl-4 pr-2'}" data-stack-header={name} data-fold-closed={closed || undefined}>
		<button
			type="button"
			class="absolute inset-0 rounded hover:bg-surface-2/60 focus-visible:outline focus-visible:outline-1 focus-visible:-outline-offset-1 focus-visible:outline-signal"
			aria-label={title}
			aria-expanded={!closed}
			onclick={() => toggleFold(name)}
			data-stack-fold={name}
		></button>
		{#if !sheet}
			<Icon name={name === 'layers' ? 'editor-layers' : 'sliders'} className="h-3.5 w-3.5 flex-none text-fg-muted" />
		{/if}
		<h2 class="{sheet ? 'text-lg' : 'text-base'} font-semibold text-fg">{title}</h2>
		<span class="font-mono text-sm tabular-nums text-fg-subtle" data-stack-count={name}>{count}</span>
		<span class="flex-1"></span>
		<span class="relative z-10 flex items-center gap-0.5" data-stack-header-actions={name}>
			{#if name === 'layers'}
				{#if headerActions && !closed}
					{@render headerActions()}
				{/if}
				{#if sheet}
					<IconButton icon="editor-undo" label={undoLabel(stack) ? `Undo ${undoLabel(stack)}` : 'Undo'} size="sm" disabled={!canUndo(stack)} onclick={() => onundo?.()} />
					<IconButton icon="editor-redo" label={redoLabel(stack) ? `Redo ${redoLabel(stack)}` : 'Redo'} size="sm" disabled={!canRedo(stack)} onclick={() => onredo?.()} />
				{:else if moreItems.length && !closed}
					<Menu label="Edit stack options" icon="chat-more" items={moreItems} onselect={(id) => onmore?.(id)} />
				{/if}
			{/if}
		</span>
		<Icon name="chevron-down" className="h-3.5 w-3.5 flex-none text-fg-muted transition-transform {closed ? '-rotate-90' : ''}" />
	</div>
{/snippet}

{#snippet layeredView()}
	{@render sectionHeader('layers')}
	<div class="min-h-0 flex-initial {sheet ? '' : 'overflow-y-auto pb-2'} {shut ? 'hidden' : ''}" data-stack-rows>
		{#if !folds.layers}
			<div class={sheet ? '' : 'px-2'} role="list" aria-label="Layers" data-stack-section="layers">
				{#each layerRows as row (row.key)}
					{#if liveLayer && liveAnchor === row.key}
						{@render liveRowView(liveLayer)}
					{/if}
					{@render stackRow(row)}
				{/each}
			</div>
		{/if}
		<div class={sheet ? 'mt-2' : 'mt-1'}>
			{@render sectionHeader('edits')}
		</div>
		{#if !folds.edits}
			<div class={sheet ? '' : 'px-2'} role="list" aria-label="Edits" data-stack-section="edits">
				{#each editRows as row (row.key)}
					{@render stackRow(row)}
				{/each}
			</div>
			<p class="px-4 pb-1 pt-1.5 text-sm text-fg-subtle" data-stack-edits-hint>
				{editRows.length ? 'Edits apply to the whole layered picture.' : 'No edits yet. Looks, light, crop and resize land here and apply to the whole layered picture.'}
			</p>
		{/if}
	</div>
{/snippet}

<section class="flex min-h-0 flex-col {sheet ? '' : `stack-panel ${shut ? 'h-11 overflow-hidden' : layered ? (bothFolded ? 'h-auto max-h-[62%]' : 'h-[360px] max-h-[62%]') : 'h-[236px]'} flex-none border-t border-line`}" aria-label={layered ? 'Layers and edits' : 'Edits'} data-edit-stack={variant} data-folded={shut || undefined} data-stack-layered={layered || undefined}>
	{#if layered}
		{@render layeredView()}
	{:else}
		{#if sheet}
			<div class="flex flex-none items-center gap-2 px-2 pb-2.5 pt-1">
				<h2 class="text-lg font-semibold text-fg">Edits</h2>
				<span class="font-mono text-sm tabular-nums text-fg-subtle" data-stack-count>{applied}</span>
				<span class="flex-1"></span>
				{#if headerActions}
					{@render headerActions()}
				{/if}
				<IconButton icon="editor-undo" label={undoLabel(stack) ? `Undo ${undoLabel(stack)}` : 'Undo'} size="sm" disabled={!canUndo(stack)} onclick={() => onundo?.()} />
				<IconButton icon="editor-redo" label={redoLabel(stack) ? `Redo ${redoLabel(stack)}` : 'Redo'} size="sm" disabled={!canRedo(stack)} onclick={() => onredo?.()} />
			</div>
		{:else}
			<div class="flex h-11 flex-none items-center gap-2 pl-4 pr-2">
				<Icon name="editor-layers" className="h-3.5 w-3.5 flex-none text-fg-muted" />
				<h2 class="text-base font-semibold text-fg">Edits</h2>
				<span class="font-mono text-sm tabular-nums text-fg-subtle" data-stack-count>{applied}{undone ? ` + ${undone} undone` : ''}</span>
				<span class="flex-1"></span>
				{#if headerActions && !shut}
					{@render headerActions()}
				{/if}
				{#if moreItems.length && !shut}
					<Menu label="Edit stack options" icon="chat-more" items={moreItems} onselect={(id) => onmore?.(id)} />
				{/if}
				<IconButton
					icon={shut ? 'chevron-right' : 'chevron-down'}
					label={shut ? 'Show edits' : 'Hide edits'}
					size="sm"
					ariaExpanded={!shut}
					onclick={() => (folded && !collapsed ? (opened = !opened) : (collapsed = !collapsed))}
					data-stack-fold
				/>
			</div>
		{/if}

	<div class="min-h-0 flex-initial {sheet ? '' : 'overflow-y-auto px-2 pb-2'} {shut ? 'hidden' : ''}" role="list" data-stack-rows>
		{#each shownRows as row (row.key)}
			{@render stackRow(row)}
		{/each}
	</div>
	{/if}

	{#if undone && !stack.pending && !sheet}
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
