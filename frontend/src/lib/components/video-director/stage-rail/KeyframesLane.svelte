<script lang="ts">
	import type { RailModel } from './railModel';
	import { resolveKeyframeDrag, isKeyframeLocked } from './railModel';
	import { railSelection, selectRailObject, isRailObjectSelected } from './railSelection';
	import { attachDrag } from '../timelineCore';

	const LANE_HEIGHT = 30;

	let {
		model,
		zoom,
		onMove
	}: {
		model: RailModel;
		zoom: number;
		onMove?: (id: string, atSeconds: number) => void;
	} = $props();

	let draggingId = $state<string | null>(null);

	function startDrag(id: string, originSeconds: number, e: PointerEvent) {
		if (!onMove) return;
		e.stopPropagation();
		draggingId = id;
		const originClientX = e.clientX;
		attachDrag(
			(move) => {
				const proposed = originSeconds + (move.clientX - originClientX) / zoom;
				onMove(id, resolveKeyframeDrag(proposed, model.snapTargets, model.totalSeconds));
			},
			() => {
				draggingId = null;
			}
		);
	}
</script>

<div class="relative rounded bg-canvas ring-1 ring-inset ring-surface-2" style="height: {LANE_HEIGHT}px">
	{#if draggingId != null}
		{#each model.snapTargets as target (target.label)}
			<span
				class="pointer-events-none absolute -top-1 -bottom-1 w-px border-l border-dashed border-line-strong"
				style="left: {target.atSeconds * zoom}px"
			></span>
		{/each}
	{/if}
	{#each model.keyframes as kf (kf.id)}
		{@const selected = isRailObjectSelected($railSelection, 'keyframe', kf.id)}
		{@const locked = isKeyframeLocked(kf.role)}
		<button
			type="button"
			class="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 transition-colors
				{selected ? 'bg-signal ring-4 ring-signal/20' : kf.hasMedia ? 'bg-fg' : 'bg-surface-3 ring-1 ring-inset ring-line-strong'}
				{onMove && !locked ? 'cursor-grab' : ''}"
			style="left: {kf.atSeconds * zoom}px"
			title="{kf.atSeconds.toFixed(2)}s{locked ? ' — locked to its shot edge' : kf.snapped && kf.snappedToLabel ? ` — snapped to ${kf.snappedToLabel}` : ' — free'}"
			onclick={() => selectRailObject('keyframe', kf.id)}
			onpointerdown={(e) => !locked && startDrag(kf.id, kf.atSeconds, e)}
		></button>
	{/each}
	<span class="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 font-mono text-2xs tabular-nums text-fg-subtle">
		{model.keyframes.length}{#if model.maxKeyframes != null} / {model.maxKeyframes}{/if}
	</span>
</div>
