<script lang="ts">
	// A keyframe (chain edge/anywhere, or timeline first/last/free), staged in
	// console.html's `.stage-kf` anatomy: the image well on the left, Role /
	// Time / Strength / Source fields and a Remove action on the right.
	//
	// Deviations from the static mock (both preserve existing functionality
	// rather than fork it -- see PLAN.md "existing editing must keep working"):
	//  - The well renders the existing DirectorMediaSlot widget (fill mode)
	//    instead of a flat background-image box with a separate "Replace
	//    image" button. DirectorMediaSlot already owns upload/library/history/
	//    "from form" and its own replace affordance; duplicating that as a
	//    second code path for the same mutation would fork behaviour the mock
	//    doesn't need to re-litigate. Only Remove is a distinct button here
	//    (clears the well; matches the old component's `remove()`).
	//  - The mock has no "Snap to" row of its own, but a mock's silence is
	//    about how things look, not what exists -- named-landmark snapping is
	//    kept as a quiet row of `.chip`-geometry buttons under the Time
	//    field, using the mock's own chip anatomy rather than the old
	//    component's ad-hoc ring-button styling.
	//  - Strength renders as a real native <input type=range> (accent-colour
	//    thumb/track) rather than the mock's static two-layer track/fill bar
	//    -- the mock's bar has no drag ergonomics as an actual HTML control.
	import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { StageKeyframeModel } from './stageModel';
	import { withChainKeyframeMedia, withTimelineKeyframeMedia, withKeyframeStrength, withChainEdgeKeyframeMedia, withChainEdgeKeyframeStrength, mediaFileLabel } from './stageModel';
	import { isChainEdgeKeyframeId, resolveDirectorMediaDisplay } from '$lib/utils/videoDirector';
	import { withChainKeyframeAt, withTimelineKeyframeAt, isKeyframeLocked, deriveRailModel, chainFilmSecondsFromLocal } from './railModel';
	import { clamp } from '../timelineCore';
	import DirectorMediaSlot from '../DirectorMediaSlot.svelte';

	let {
		model,
		doc,
		caps,
		timelineShotId,
		formData,
		onDoc
	}: {
		model: StageKeyframeModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		/** Which shot's own keyframe list `model.id` addresses -- ignored for
		 * chain routing. */
		timelineShotId: string;
		formData: Record<string, unknown> | null | undefined;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let isChainEdge = $derived(isChainEdgeKeyframeId(model.id));
	let isChain = $derived(model.role === 'keyframe');
	// Mirrors KeyframesLane's drag lock (isKeyframeLocked) -- a locked anchor's
	// time is fixed to its shot's edge, so the Time field is read-only for it.
	let locked = $derived(isKeyframeLocked(model.role));
	let roleLabel = $derived(model.role === 'first' ? 'Start' : model.role === 'last' ? 'End' : model.role === 'keyframe' ? 'Anywhere' : 'Free');
	let display = $derived(resolveDirectorMediaDisplay(model.media, formData));
	let imageUrl = $derived(display.kind === 'embedded' || display.kind === 'form_ref' ? display.media.url : null);
	let sourceLabel = $derived(mediaFileLabel(model.media) ?? '—');

	function setMedia(value: DirectorMediaValue | null) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeMedia(doc, model.id, value));
		} else if (isChain) {
			onDoc(withChainKeyframeMedia(doc, model.id, value));
		} else {
			onDoc(withTimelineKeyframeMedia(doc, timelineShotId, model.id, model.role as 'first' | 'last' | 'free', model.atSeconds, value));
		}
	}
	function applyTime(seconds: number) {
		// `seconds` is always shot-local here (the Time field/Snap chips both
		// read `model.atSeconds`/`model.snapTargets`, which are shot-local --
		// see stageModel.ts's own doc comment). A chain 'anywhere' keyframe's
		// storage (`chain.keyframes[].at`) is FILM time, so it's converted
		// back through the same landing shot's own window and clamped to
		// [0, that shot's own length] first -- a snapped/typed value can then
		// never resolve into a different shot (maintainer bug report, 09-04).
		if (isChain) {
			if (!model.landing) return; // locked edges never reach here (see `locked` below)
			const rail = deriveRailModel(doc, caps);
			const clamped = clamp(seconds, 0, rail.fps > 0 ? model.landing.localTotalFrames / rail.fps : 0);
			onDoc(withChainKeyframeAt(doc, model.id, chainFilmSecondsFromLocal(rail, model.landing.shotIndex, clamped)));
		} else {
			const rail = deriveRailModel(doc, caps, timelineShotId);
			const clamped = clamp(seconds, 0, rail.fps > 0 ? model.totalFrames / rail.fps : 0);
			onDoc(withTimelineKeyframeAt(doc, timelineShotId, model.id, clamped));
		}
	}
	function setTime(e: Event) {
		const seconds = parseFloat((e.currentTarget as HTMLInputElement).value);
		if (!Number.isFinite(seconds)) return;
		applyTime(seconds);
	}
	function snapTo(atSeconds: number) {
		applyTime(atSeconds);
	}
	function setStrength(strength: number) {
		if (isChainEdge) {
			onDoc(withChainEdgeKeyframeStrength(doc, model.id, strength));
		} else {
			onDoc(withKeyframeStrength(doc, caps, timelineShotId, model.id, strength));
		}
	}
	function remove() {
		setMedia(null);
	}
</script>

<div class="stage-kf">
	<div class="stage-kf-img" class:empty={!imageUrl}>
		<DirectorMediaSlot name="{model.id}-media" value={model.media} {formData} kind="image" fill onChange={setMedia} config={{ accept: 'image/*' }} />
	</div>
	<div class="stage-kf-data">
		<div class="stage-field">
			<span class="fl">Role</span>
			<span class="chip">{roleLabel}</span>
		</div>
		<div class="stage-field">
			<span class="fl">Time</span>
			{#if locked}
				<span class="fv tabular">{model.atSeconds.toFixed(2)} s</span>
			{:else}
				<input class="stage-time-input tabular" value={model.atSeconds.toFixed(2)} onchange={setTime} />
				{#if model.snapTargets.length > 0}
					<div class="snap-row">
						<span class="snap-label">Snap to</span>
						{#each model.snapTargets as target (target.label)}
							<button
								type="button"
								class="chip snap-chip"
								class:active={model.snapped && model.snappedToLabel === target.label}
								onclick={() => snapTo(target.atSeconds)}
							>
								{target.label}
								<span class="mono tabular">{target.atSeconds.toFixed(2)}s</span>
							</button>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
		<div class="stage-field">
			<span class="fl">Strength</span>
			<div class="strength-row">
				<input
					type="range"
					min="0"
					max="1"
					step="0.01"
					class="strength-slider"
					disabled={!model.media}
					title={model.media ? undefined : 'Attach an image first'}
					value={model.strength}
					oninput={(e) => setStrength(parseFloat((e.currentTarget as HTMLInputElement).value))}
				/>
				<span class="mono tabular strength-value">{model.strength.toFixed(2)}</span>
			</div>
		</div>
		<div class="stage-field">
			<span class="fl">Source</span>
			<span class="fv">{sourceLabel}</span>
		</div>
		{#if model.media}
			<div class="stage-actions">
				<button type="button" class="btn" onclick={remove}>
					<svg class="icon" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M6 18L18 6M6 6l12 12" /></svg>
					Remove
				</button>
			</div>
		{/if}
	</div>
</div>

<style>
	.stage-kf {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
	}
	.stage-kf-img {
		flex: 1 1 340px;
		max-width: 460px;
		aspect-ratio: 16 / 9;
		border-radius: 8px;
		box-shadow: var(--shadow-raised);
		position: relative;
		overflow: hidden;
	}
	.stage-kf-img.empty {
		background: rgb(var(--canvas));
		border: 1px dashed rgb(var(--line-strong));
	}
	.stage-kf-data {
		flex: 1 1 220px;
		min-width: 220px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.stage-field {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 4px;
	}
	.stage-field .fl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
	}
	.stage-field .fv {
		font-size: 12.5px;
		color: rgb(var(--fg));
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 22px;
		padding: 0 8px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted));
	}
	.stage-time-input {
		width: 90px;
		height: 27px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
		font-size: 12px;
		font-family: 'IBM Plex Mono', monospace;
		padding: 0 9px;
	}
	.snap-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		margin-top: 2px;
	}
	.snap-label {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
		margin-right: 2px;
	}
	.snap-chip {
		cursor: pointer;
	}
	.snap-chip:hover {
		color: rgb(var(--fg));
		border-color: rgb(var(--line-hover));
	}
	.snap-chip.active {
		color: rgb(var(--signal));
		border-color: rgb(var(--signal) / 0.5);
		background: rgb(var(--signal) / 0.1);
	}
	.strength-row {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.strength-slider {
		width: 140px;
		accent-color: rgb(var(--signal));
	}
	.strength-slider:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.strength-value {
		font-size: 11.5px;
		color: rgb(var(--fg-muted));
	}
	.mono {
		font-family: 'IBM Plex Mono', monospace;
	}
	.tabular {
		font-variant-numeric: tabular-nums;
	}
	.stage-actions {
		display: flex;
		gap: 8px;
		margin-top: 2px;
	}
	.btn {
		height: 27px;
		padding: 0 10px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
		font-size: 12px;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
	}
	.btn:hover {
		background: rgb(var(--surface-3));
	}
	.icon {
		width: 12px;
		height: 12px;
		stroke: currentColor;
		fill: none;
		flex: none;
	}
</style>
