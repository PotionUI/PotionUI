<script lang="ts">
	import type { RailModel } from './railModel';
	import { railSelection, selectRailObject, isRailObjectSelected } from './railSelection';
	import { attachDrag } from '../timelineCore';
	import Icon from '$lib/components/Icon.svelte';

	const LANE_HEIGHT = 56;
	const MIN_BLOCK_WIDTH = 3;

	let {
		model,
		zoom,
		onResizeEdge
	}: {
		model: RailModel;
		zoom: number;
		/** Timeline (LTX) routing only -- a block edge was dragged to a new,
		 * unclamped absolute time; the caller resolves the non-overlap clamp. */
		onResizeEdge?: (id: string, edge: 'start' | 'end', proposedSeconds: number) => void;
	} = $props();

	function startEdgeDrag(id: string, edge: 'start' | 'end', originSeconds: number, e: PointerEvent) {
		if (!onResizeEdge) return;
		e.stopPropagation();
		const originClientX = e.clientX;
		attachDrag((move) => {
			const deltaSeconds = (move.clientX - originClientX) / zoom;
			onResizeEdge(id, edge, originSeconds + deltaSeconds);
		});
	}
</script>

<div class="relative rounded bg-canvas ring-1 ring-inset ring-surface-2" style="height: {LANE_HEIGHT}px">
	{#each model.shots as shot (shot.id)}
		{@const left = shot.startSeconds * zoom}
		{@const width = Math.max(MIN_BLOCK_WIDTH, shot.contributedSeconds * zoom)}
		{@const selected = isRailObjectSelected($railSelection, 'shot', shot.id)}
		{@const overCap = shot.overCapBy > 0}
		<button
			type="button"
			class="absolute top-0 flex flex-col overflow-hidden rounded px-2.5 py-1.5 text-left transition-colors
				{overCap ? 'bg-danger/10 ring-1 ring-inset ring-danger' : selected ? 'ring-1 ring-inset ring-signal' : 'bg-surface-1 ring-1 ring-inset ring-line-strong hover:bg-surface-2 hover:ring-line-hover'}"
			style="left: {left}px; width: {width}px; height: {LANE_HEIGHT}px{selected && !overCap
				? '; background: linear-gradient(180deg, rgb(var(--signal) / 0.16), rgb(var(--signal) / 0.05))'
				: ''}"
			onclick={() => selectRailObject('shot', shot.id)}
		>
			<span class="flex items-center gap-1.5 overflow-hidden">
				<span class="font-mono text-2xs tabular-nums {selected ? 'text-signal' : 'text-fg-subtle'}"
					>{String(shot.index + 1).padStart(2, '0')}</span
				>
				<span class="truncate text-xs font-medium text-fg">{shot.label}</span>
			</span>
			<span class="mt-1 font-mono text-2xs tabular-nums {overCap ? 'text-danger' : 'text-fg-subtle'}">
				{shot.totalFrames} f · {shot.contributedSeconds.toFixed(2)}s{#if shot.hasOverlapIn}
					<span class="text-fg-subtle"> · {shot.contributedFrames} new</span>
				{/if}
			</span>
		</button>

		{#if overCap && shot.capLocalFraction != null}
			{@const dangerLeft = left + shot.capLocalFraction * width}
			<div
				class="pointer-events-none absolute top-0"
				style="left: {dangerLeft}px; width: {left + width - dangerLeft}px; height: {LANE_HEIGHT}px; background: repeating-linear-gradient(45deg, rgb(var(--danger) / 0.3) 0px, rgb(var(--danger) / 0.3) 3px, transparent 3px, transparent 7px)"
			></div>
			<div class="pointer-events-none absolute top-0 w-px bg-danger" style="left: {dangerLeft}px; height: {LANE_HEIGHT}px"></div>
		{/if}

		{#if onResizeEdge}
			<div
				class="absolute top-0 w-1.5 cursor-ew-resize"
				style="left: {left - 3}px; height: {LANE_HEIGHT}px"
				onpointerdown={(e) => startEdgeDrag(shot.id, 'start', shot.startSeconds, e)}
				role="presentation"
			></div>
			<div
				class="absolute top-0 w-1.5 cursor-ew-resize"
				style="left: {left + width - 3}px; height: {LANE_HEIGHT}px"
				onpointerdown={(e) => startEdgeDrag(shot.id, 'end', shot.startSeconds + shot.contributedSeconds, e)}
				role="presentation"
			></div>
		{/if}
	{/each}

	{#each model.seams as seam (seam.id)}
		{#if seam.shoulderStartSeconds != null}
			<div
				class="pointer-events-none absolute top-0"
				style="left: {seam.shoulderStartSeconds * zoom}px; width: {(seam.atSeconds - seam.shoulderStartSeconds) * zoom}px; height: {LANE_HEIGHT}px; background: repeating-linear-gradient(45deg, rgb(var(--signal) / 0.16) 0px, rgb(var(--signal) / 0.16) 3px, transparent 3px, transparent 7px)"
			></div>
		{/if}
		{@const selected = isRailObjectSelected($railSelection, 'seam', seam.id)}
		<button
			type="button"
			class="absolute top-1/2 z-10 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-canvas ring-1 ring-inset transition-colors
				{selected ? 'ring-signal' : seam.kind === 'continue' ? 'ring-line-hover hover:ring-signal' : 'ring-line-hover hover:ring-fg'}"
			style="left: {seam.atSeconds * zoom}px"
			onclick={() => selectRailObject('seam', seam.id)}
			title={seam.kind === 'continue' ? `Continue · ${seam.overlapFrames} f` : 'Hard cut'}
		>
			<Icon name={seam.kind === 'continue' ? 'refresh' : 'close'} className="w-3.5 h-3.5 {seam.kind === 'continue' ? 'text-signal' : 'text-fg'}" />
		</button>
	{/each}
</div>
