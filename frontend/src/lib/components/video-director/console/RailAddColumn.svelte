<script lang="ts">
	// `.rail-add-col` from console.html -- one `+` per growable lane, aligned
	// to that lane's height. Copies `stage-rail/Rail.svelte`'s own
	// `addButton` snippet (lines 273-284) and its disabled-with-title gating
	// (lines 249-265), just against percent-of-shot geometry instead of the
	// film rail's px/zoom.
	//
	// Each button adds "at the end / next free slot" (W1-BRIEF.md) rather
	// than at a specific pointer position -- that's `nextOpenSlotSeconds`
	// below, the largest open interval's midpoint. A lane whose profile has
	// zero free-placement capability ever (Wan: START/END anchors only, no
	// free keyframes) renders a plain spacer instead of a button, matching
	// console.html's Wan frame (`<span style="height:64px"></span>`, no
	// `<button>` at all) -- distinct from "temporarily at cap", which still
	// renders a disabled button with the reason as its title.
	import type { RailBeat, RailKeyframeMark } from './shotRailModel';
	import { nextOpenSlotSeconds } from './nextSlot';

	const PROMPT_H = 46;
	const KF_H = 64;
	const AUDIO_H = 32;

	let {
		durationSeconds,
		prompt,
		keyframes,
		audio,
		onAddBeat,
		onAddKeyframe,
		onAddAudio
	}: {
		durationSeconds: number;
		prompt: { beats: RailBeat[]; canAdd: boolean; addDisabledReason: string | null } | null;
		keyframes: { marks: RailKeyframeMark[]; count: number; cap: number | null; canAdd: boolean } | null;
		audio: { canAdd: boolean } | null;
		onAddBeat: (atSeconds: number) => void;
		onAddKeyframe: (atSeconds: number) => void;
		onAddAudio: () => void;
	} = $props();

	function addBeat() {
		if (!prompt) return;
		const occupied = prompt.beats
			.filter((b) => !b.global)
			.map((b) => ({
				start: (b.startPercent / 100) * durationSeconds,
				end: ((b.startPercent + b.widthPercent) / 100) * durationSeconds
			}));
		onAddBeat(nextOpenSlotSeconds(occupied, durationSeconds));
	}

	function addKeyframe() {
		if (!keyframes) return;
		const occupied = keyframes.marks.map((m) => {
			const t = (m.atPercent / 100) * durationSeconds;
			return { start: t, end: t };
		});
		onAddKeyframe(nextOpenSlotSeconds(occupied, durationSeconds));
	}
</script>

<div class="rail-add-col">
	<div class="ruler-spacer"></div>
	{#if prompt}
		<button
			type="button"
			class="add-btn {!prompt.canAdd ? 'disabled' : ''}"
			style="height:{PROMPT_H}px"
			disabled={!prompt.canAdd}
			title={!prompt.canAdd ? (prompt.addDisabledReason ?? 'Add prompt beat') : 'Add prompt beat'}
			aria-label="Add prompt beat"
			onclick={addBeat}
		>
			+
		</button>
	{/if}
	{#if keyframes}
		{#if keyframes.cap === 0}
			<span style="height:{KF_H}px"></span>
		{:else}
			<button
				type="button"
				class="add-btn {!keyframes.canAdd ? 'disabled' : ''}"
				style="height:{KF_H}px"
				disabled={!keyframes.canAdd}
				title={!keyframes.canAdd
					? `Keyframe cap reached${keyframes.cap != null ? ` (${keyframes.cap})` : ''}`
					: 'Add keyframe'}
				aria-label="Add keyframe"
				onclick={addKeyframe}
			>
				+
			</button>
		{/if}
	{/if}
	{#if audio}
		<button
			type="button"
			class="add-btn {!audio.canAdd ? 'disabled' : ''}"
			style="height:{AUDIO_H}px"
			disabled={!audio.canAdd}
			title={!audio.canAdd ? "Adding audio isn't available for this shot" : 'Add audio'}
			aria-label="Add audio"
			onclick={onAddAudio}
		>
			+
		</button>
	{/if}
</div>

<style>
	.rail-add-col {
		flex: none;
		width: 30px;
		margin-left: 8px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ruler-spacer {
		height: 22px;
	}
	.add-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 30px;
		border-radius: 4px;
		background: rgb(var(--canvas));
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
		color: rgb(var(--fg-subtle));
		cursor: pointer;
		font-size: 15px;
		line-height: 1;
		transition:
			color 0.15s,
			box-shadow 0.15s;
	}
	.add-btn:hover {
		color: rgb(var(--fg));
		box-shadow: inset 0 0 0 1px rgb(var(--signal));
	}
	.add-btn.disabled {
		pointer-events: none;
		opacity: 0.4;
	}
</style>
