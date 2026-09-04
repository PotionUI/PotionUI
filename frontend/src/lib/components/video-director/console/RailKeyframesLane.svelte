<script lang="ts">
	// `.kf-lane` from console.html: locked START/END `.kf-anchor`s (not
	// draggable, still selectable) plus free keyframes, each drawn as a
	// `.kf-free` diamond (the grab handle) sitting above its own `.kf-thumb40`
	// preview. The floating `.insert-cue` from the prompt lane has no room to
	// float above this lane (only an 8px row-gap separates them), so it uses
	// the `.in-lane` variant that sits inside the lane's own top sliver
	// instead (see console.html's CSS comment on `.insert-cue.in-lane`).
	import type { RailKeyframeMark } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';
	import { attachDrag } from '../timelineCore';

	const NUDGE_SECONDS = 0.25;

	let {
		shotId,
		durationSeconds,
		marks,
		canAdd,
		selection,
		onSelect,
		onAdd,
		onMove,
		snapFraction
	}: {
		shotId: string;
		durationSeconds: number;
		marks: RailKeyframeMark[];
		canAdd: boolean;
		selection: ConsoleSelection;
		onSelect: (sel: ConsoleSelection) => void;
		onAdd: (atSeconds: number) => void;
		onMove: (id: string, atSeconds: number) => void;
		/** `(fraction) => railTimeFromFraction(rail, fraction)`, bound by ShotRail.svelte. */
		snapFraction: (fraction: number) => number;
	} = $props();

	let laneEl: HTMLDivElement | undefined = $state();
	let hoverFraction = $state<number | null>(null);
	let dragging = $state(false);

	function isSelected(id: string): boolean {
		return selection?.shotId === shotId && selection.kind === 'keyframe' && selection.id === id;
	}

	function fractionFromClientX(clientX: number): number | null {
		if (!laneEl) return null;
		const rect = laneEl.getBoundingClientRect();
		if (rect.width <= 0) return null;
		return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
	}

	function handlePointerMove(e: PointerEvent) {
		if (dragging || !canAdd) {
			hoverFraction = null;
			return;
		}
		hoverFraction = fractionFromClientX(e.clientX);
	}

	function handlePointerLeave() {
		hoverFraction = null;
	}

	function handleLaneClick(e: MouseEvent) {
		if (!canAdd) return;
		const fraction = fractionFromClientX(e.clientX);
		if (fraction == null) return;
		onAdd(snapFraction(fraction));
	}

	function selectMark(e: MouseEvent, id: string) {
		e.stopPropagation();
		onSelect({ shotId, kind: 'keyframe', id });
	}

	function startDrag(mark: RailKeyframeMark, e: PointerEvent) {
		if (mark.kind !== 'free') return;
		e.stopPropagation();
		e.preventDefault();
		if (!laneEl) return;
		const rect = laneEl.getBoundingClientRect();
		if (rect.width <= 0) return;
		dragging = true;
		hoverFraction = null;
		const originClientX = e.clientX;
		const originSeconds = (mark.atPercent / 100) * durationSeconds;
		attachDrag(
			(move) => {
				const deltaSeconds = ((move.clientX - originClientX) / rect.width) * durationSeconds;
				onMove(mark.id, originSeconds + deltaSeconds);
			},
			() => {
				dragging = false;
			}
		);
	}

	function nudge(e: KeyboardEvent, mark: RailKeyframeMark) {
		if (mark.kind !== 'free') return;
		if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
		e.preventDefault();
		const atSeconds = (mark.atPercent / 100) * durationSeconds;
		onMove(mark.id, atSeconds + (e.key === 'ArrowLeft' ? -NUDGE_SECONDS : NUDGE_SECONDS));
	}

	let hoverSeconds = $derived(hoverFraction != null ? snapFraction(hoverFraction) : null);
	let hoverPercent = $derived(
		hoverSeconds != null && durationSeconds > 0 ? (hoverSeconds / durationSeconds) * 100 : null
	);
</script>

<div
	class="kf-lane {canAdd ? 'can-add' : ''}"
	bind:this={laneEl}
	role="presentation"
	onpointermove={handlePointerMove}
	onpointerleave={handlePointerLeave}
	onclick={handleLaneClick}
>
	{#each marks as mark (mark.id)}
		{#if mark.kind === 'start' || mark.kind === 'end'}
			<button
				type="button"
				class="kf-anchor {isSelected(mark.id) ? 'selected' : ''}"
				style="{mark.kind === 'start' ? 'left:0' : 'right:0;left:auto'}{mark.thumbUrl
					? `;background-image:url(${mark.thumbUrl})`
					: ''}"
				onclick={(e) => selectMark(e, mark.id)}
			>
				<span class="tag" style={mark.thumbUrl ? undefined : 'color:rgb(var(--fg-subtle))'}>{mark.label}</span>
			</button>
		{:else}
			<button
				type="button"
				class="kf-free {isSelected(mark.id) ? 'selected' : ''}"
				style="left:{mark.atPercent}%"
				title="{mark.label} — drag to move, arrow keys to nudge"
				onclick={(e) => selectMark(e, mark.id)}
				onpointerdown={(e) => startDrag(mark, e)}
				onkeydown={(e) => nudge(e, mark)}
			></button>
			<button
				type="button"
				class="kf-thumb40 {isSelected(mark.id) ? 'selected' : ''} {mark.empty ? 'is-empty' : ''}"
				style="left:{mark.atPercent}%{mark.thumbUrl ? `;background-image:url(${mark.thumbUrl})` : ''}"
				title={mark.label}
				tabindex="-1"
				aria-hidden="true"
				onclick={(e) => selectMark(e, mark.id)}
				onpointerdown={(e) => startDrag(mark, e)}
			></button>
		{/if}
	{/each}
	{#if hoverPercent != null && hoverSeconds != null}
		<div class="insert-cue in-lane" style="left:{hoverPercent}%">
			<span class="dot2"></span>
			<span class="pill">+ keyframe at {hoverSeconds.toFixed(2)} s</span>
		</div>
	{/if}
</div>

<style>
	.kf-lane {
		height: 64px;
		position: relative;
		background: rgb(var(--surface-1) / 0.5);
		border-radius: 4px;
		box-shadow: var(--shadow-well);
	}
	.kf-lane.can-add {
		cursor: pointer;
	}
	.kf-anchor {
		position: absolute;
		top: 10px;
		width: 40px;
		height: 40px;
		border-radius: 4px;
		background-size: cover;
		background-position: center;
		border: 1px solid rgb(var(--line-strong));
		cursor: pointer;
	}
	.kf-anchor.selected {
		border: 2px solid rgb(var(--signal));
		box-shadow: 0 0 0 3px rgb(var(--signal) / 0.2);
	}
	.kf-anchor .tag {
		position: absolute;
		left: 2px;
		bottom: 2px;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 7.5px;
		letter-spacing: 0.03em;
		color: rgb(var(--fg));
		text-shadow: 0 1px 2px rgb(0 0 0 / 0.8);
	}
	.kf-free {
		position: absolute;
		top: 0;
		width: 10px;
		height: 10px;
		padding: 0;
		background: rgb(var(--fg));
		border: none;
		border-radius: 1px;
		transform: translate(-50%, 0) rotate(45deg);
		cursor: grab;
	}
	.kf-free.selected {
		background: rgb(var(--signal));
		box-shadow: 0 0 0 4px rgb(var(--signal) / 0.2);
	}
	.kf-thumb40 {
		position: absolute;
		top: 18px;
		width: 40px;
		height: 40px;
		padding: 0;
		border-radius: 4px;
		background-size: cover;
		background-position: center;
		border: 1px solid rgb(var(--line-strong));
		transform: translateX(-50%);
		cursor: pointer;
	}
	.kf-thumb40.selected {
		border: 2px solid rgb(var(--signal));
		box-shadow: 0 0 0 3px rgb(var(--signal) / 0.2);
	}
	/* Not in the static template, which never shows an unset free keyframe --
	   dashed well matches the `.stage-kf-img.empty`/`.global-fill` idiom used
	   elsewhere for "nothing here yet" wells. */
	.kf-thumb40.is-empty {
		background-color: rgb(var(--canvas));
		border-style: dashed;
	}
	.insert-cue.in-lane {
		position: absolute;
		top: 2px;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 4px;
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
