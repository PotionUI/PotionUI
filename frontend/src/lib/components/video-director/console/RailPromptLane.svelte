<script lang="ts">
	// `.prompt-lane` from console.html: real prompt beats (`.beat-b`) plus,
	// wherever a beat doesn't cover the shot, a dashed `.global-fill` gap
	// filler (the deriveShotRail contract folds the gap into `beats` too,
	// marked `global: true`, so this component never has to compute gaps
	// itself). Hovering a growable lane shows an insert cue at the snapped
	// time; clicking the lane background (not a real beat) adds a beat there.
	// Chain profiles arrive with `canAdd: false` (one full-span beat, no
	// per-beat add) -- see PLAN.md D3 -- so the hover cue and click-to-add
	// simply don't activate; nothing here needs to know *why*.
	import type { RailBeat } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';
	import { attachDrag } from '../timelineCore';

	let {
		shotId,
		durationSeconds,
		beats,
		canAdd,
		addDisabledReason,
		selection,
		onSelect,
		onAdd,
		onResizeBeat,
		snapFraction
	}: {
		shotId: string;
		durationSeconds: number;
		beats: RailBeat[];
		canAdd: boolean;
		addDisabledReason: string | null;
		selection: ConsoleSelection;
		onSelect: (sel: ConsoleSelection) => void;
		onAdd: (atSeconds: number) => void;
		onResizeBeat: (id: string, edge: 'start' | 'end', atSeconds: number) => void;
		/** `(fraction) => railTimeFromFraction(rail, fraction)`, bound by ShotRail.svelte -- keeps this leaf from needing the whole ShotRailModel just to snap a hover position. */
		snapFraction: (fraction: number) => number;
	} = $props();

	let laneEl: HTMLDivElement | undefined = $state();
	let hoverFraction = $state<number | null>(null);
	let dragging = $state(false);

	let realBeats = $derived(beats.filter((b) => !b.global));

	function beatIndex(id: string): number {
		return realBeats.findIndex((b) => b.id === id) + 1;
	}

	function isSelected(id: string): boolean {
		return selection?.shotId === shotId && selection.kind === 'beat' && selection.id === id;
	}

	function fractionFromClientX(clientX: number): number | null {
		if (!laneEl) return null;
		const rect = laneEl.getBoundingClientRect();
		if (rect.width <= 0) return null;
		return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
	}

	// Maintainer bug (09-04): the hover insert-cue tracked the pointer over
	// existing beats too, sitting visually on top of (and, before this fix,
	// occasionally the click target instead of) the block underneath. The
	// cue now hides entirely whenever the pointer sits over a real beat --
	// `pointer-events: none` on the cue itself already keeps IT out of hit
	// testing, so `e.target` here is always the real element beneath it.
	function isOverBeat(e: Event): boolean {
		return (e.target as HTMLElement).closest('.beat-b, .beat-edge') != null;
	}

	function handlePointerMove(e: PointerEvent) {
		if (dragging || !canAdd || isOverBeat(e)) {
			hoverFraction = null;
			return;
		}
		hoverFraction = fractionFromClientX(e.clientX);
	}

	function handlePointerLeave() {
		hoverFraction = null;
	}

	function handleLaneClick(e: MouseEvent) {
		// Belt-and-suspenders: `selectBeat`'s own `stopPropagation()` already
		// keeps a beat click from reaching here, but the insert must never
		// fire off a beat regardless of how the click got here.
		if (!canAdd || isOverBeat(e)) return;
		const fraction = fractionFromClientX(e.clientX);
		if (fraction == null) return;
		onAdd(snapFraction(fraction));
	}

	function selectBeat(e: MouseEvent, id: string) {
		e.stopPropagation();
		onSelect({ shotId, kind: 'beat', id });
	}

	function startEdgeDrag(id: string, edge: 'start' | 'end', originSeconds: number, e: PointerEvent) {
		e.stopPropagation();
		e.preventDefault();
		if (!laneEl) return;
		const rect = laneEl.getBoundingClientRect();
		if (rect.width <= 0) return;
		dragging = true;
		hoverFraction = null;
		const originClientX = e.clientX;
		attachDrag(
			(move) => {
				const deltaSeconds = ((move.clientX - originClientX) / rect.width) * durationSeconds;
				onResizeBeat(id, edge, originSeconds + deltaSeconds);
			},
			() => {
				dragging = false;
			}
		);
	}

	let hoverSeconds = $derived(hoverFraction != null ? snapFraction(hoverFraction) : null);
	let hoverPercent = $derived(
		hoverSeconds != null && durationSeconds > 0 ? (hoverSeconds / durationSeconds) * 100 : null
	);
</script>

<div
	class="prompt-lane {canAdd ? 'can-add' : ''}"
	bind:this={laneEl}
	role="presentation"
	title={!canAdd && addDisabledReason ? addDisabledReason : undefined}
	onpointermove={handlePointerMove}
	onpointerleave={handlePointerLeave}
	onclick={handleLaneClick}
>
	{#each beats as beat (beat.id)}
		{#if beat.global}
			<div class="global-fill" style="left:{beat.startPercent}%;width:{beat.widthPercent}%">
				<span class="lbl">Global</span>
			</div>
		{:else}
			<button
				type="button"
				class="beat-b {isSelected(beat.id) ? 'selected' : ''}"
				style="left:{beat.startPercent}%;width:{beat.widthPercent}%"
				onclick={(e) => selectBeat(e, beat.id)}
			>
				<span class="idx">{String(beatIndex(beat.id)).padStart(2, '0')}</span>
				<span class="lbl">{beat.text}</span>
			</button>
			<div
				class="beat-edge beat-edge-start"
				style="left:{beat.startPercent}%"
				onpointerdown={(e) => startEdgeDrag(beat.id, 'start', (beat.startPercent / 100) * durationSeconds, e)}
				role="presentation"
			></div>
			<div
				class="beat-edge beat-edge-end"
				style="left:{beat.startPercent + beat.widthPercent}%"
				onpointerdown={(e) =>
					startEdgeDrag(beat.id, 'end', ((beat.startPercent + beat.widthPercent) / 100) * durationSeconds, e)}
				role="presentation"
			></div>
		{/if}
	{/each}
	{#if hoverPercent != null && hoverSeconds != null}
		<div class="insert-cue" style="left:{hoverPercent}%">
			<span class="pill">+ prompt at {hoverSeconds.toFixed(2)} s</span>
			<span class="dot2"></span>
		</div>
	{/if}
</div>

<style>
	.prompt-lane {
		height: 46px;
		position: relative;
		background: rgb(var(--surface-1) / 0.5);
		border-radius: 4px;
		box-shadow: var(--shadow-well);
	}
	.prompt-lane.can-add {
		cursor: pointer;
	}
	.beat-b {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 2;
		display: flex;
		flex-direction: column;
		justify-content: center;
		padding: 0 10px;
		border-radius: 4px;
		overflow: hidden;
		background: rgb(var(--surface-1));
		border: 1px solid rgb(var(--line-strong));
		cursor: pointer;
		text-align: left;
	}
	.beat-b.selected {
		border-color: rgb(var(--signal));
		background: linear-gradient(180deg, rgb(var(--signal) / 0.16), rgb(var(--signal) / 0.05));
	}
	.beat-b .idx {
		display: block;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9.5px;
		color: rgb(var(--fg-subtle));
	}
	.beat-b.selected .idx {
		color: rgb(var(--signal));
	}
	.beat-b .lbl {
		display: block;
		font-size: 11px;
		color: rgb(var(--fg));
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		margin-top: 1px;
	}
	.global-fill {
		position: absolute;
		top: 0;
		bottom: 0;
		border: 1px dashed rgb(var(--line-strong));
		border-radius: 4px;
		display: flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		pointer-events: none;
	}
	.global-fill .lbl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-disabled));
	}
	/* Not in the static template -- the mock has no live drag; these are the
	   edge-resize handles, positioned like `stage-rail/ShotsLane.svelte`'s
	   own edge divs (thin strips straddling the beat's boundary, siblings of
	   the beat rather than nested in its <button> so the DOM stays valid). */
	.beat-edge {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 6px;
		margin-left: -3px;
		cursor: ew-resize;
	}
	.insert-cue {
		position: absolute;
		top: -26px;
		z-index: 1;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 3px;
		pointer-events: none;
	}
	.insert-cue .pill {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9px;
		white-space: nowrap;
		padding: 2px 7px;
		border-radius: 4px;
		background: rgb(var(--surface-2));
		border: 1px dashed rgb(var(--fg-subtle));
		color: rgb(var(--fg-muted));
	}
	.insert-cue .dot2 {
		width: 8px;
		height: 8px;
		border: 1.5px dashed rgb(var(--fg-subtle));
		border-radius: 1px;
		transform: rotate(45deg);
	}
</style>
