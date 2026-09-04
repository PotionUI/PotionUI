<script lang="ts">
	// A prompt beat, staged: the existing SegmentedPromptEditor for the
	// beat's own segments, plus (timeline profiles only) an editable Range
	// and a Remove beat action. Chain profiles render exactly one full-span
	// beat per shot (PLAN.md D3) -- it has no independent start/end and
	// removing it is meaningless (removing the shot removes its prompt), so
	// neither the Range row nor Remove beat render for chain routing; the
	// segments editor is the entire body there.
	//
	// `model` is the existing StageShotModel derivation (deriveStageModel's
	// 'shot' branch) -- a timeline "shot" IS a prompt beat in the new
	// console's vocabulary (PLAN.md §B); this file only re-lays out the
	// prompt-editing slice of it (no gates/footer -- those moved to the
	// Keyframes lane / ShotCard header respectively).
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import type { StageShotModel } from '../stage-rail/stageModel';
	import { withShotPromptSegments } from '../stage-rail/stageModel';
	import { resizeTimelineBlockEdge, withTimelineSegmentEdge } from '../stage-rail/railModel';
	import { applyDirectorOperations } from '$lib/utils/videoDirector';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import type { Segment } from '$lib/types/segments';

	let {
		model,
		doc,
		caps,
		timelineShotId,
		onDoc
	}: {
		model: StageShotModel;
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		/** Which shot's own beat list `model.id` addresses -- ignored for chain
		 * routing (a chain shot's prompt IS its one segment). */
		timelineShotId: string;
		onDoc: (next: VideoDirectorValue) => void;
	} = $props();

	let isTimeline = $derived(model.routing === 'timeline' && model.footer.startSeconds != null && model.footer.endSeconds != null);
	let timelineShot = $derived(caps.segmentRouting ? undefined : doc.timeline.shots.find((s) => s.id === timelineShotId));

	function updatePromptSegments(segments: Segment[]) {
		onDoc(withShotPromptSegments(doc, caps, model.id, segments, timelineShotId));
	}
	function setStart(e: Event) {
		if (!timelineShot) return;
		const raw = parseFloat((e.currentTarget as HTMLInputElement).value);
		if (!Number.isFinite(raw)) return;
		const clamped = resizeTimelineBlockEdge(timelineShot.segments, model.id, 'start', raw, timelineShot.duration);
		onDoc(withTimelineSegmentEdge(doc, timelineShotId, model.id, 'start', clamped));
	}
	function setEnd(e: Event) {
		if (!timelineShot) return;
		const raw = parseFloat((e.currentTarget as HTMLInputElement).value);
		if (!Number.isFinite(raw)) return;
		const clamped = resizeTimelineBlockEdge(timelineShot.segments, model.id, 'end', raw, timelineShot.duration);
		onDoc(withTimelineSegmentEdge(doc, timelineShotId, model.id, 'end', clamped));
	}
	function removeBeat() {
		onDoc(applyDirectorOperations(doc, [{ op: 'remove_segment', id: model.id, shot_id: timelineShotId }], caps));
	}
</script>

<div class="stage-beat">
	{#if isTimeline}
		<div class="stage-beat-head">
			<span class="fl">Range</span>
			<input class="range-input tabular" value={(model.footer.startSeconds ?? 0).toFixed(1)} onchange={setStart} />
			<span class="dash">–</span>
			<input class="range-input tabular" value={(model.footer.endSeconds ?? 0).toFixed(1)} onchange={setEnd} />
			<span class="unit">s</span>
			<div class="spacer"></div>
			<button type="button" class="btn" onclick={removeBeat}>
				<svg class="icon" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M6 18L18 6M6 6l12 12" /></svg>
				Remove beat
			</button>
		</div>
	{/if}
	<div class="editor">
		<SegmentedPromptEditor
			segments={model.promptSegments}
			label="Prompt"
			showPreview={false}
			compact
			placeholder={model.isFirst && model.isLast ? 'Describe the first shot…' : "Describe this shot's action, camera and composition…"}
			on:segmentsChange={(e) => updatePromptSegments(e.detail)}
		/>
	</div>
</div>

<style>
	.stage-beat {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.stage-beat-head {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.stage-beat-head .fl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
	}
	.dash,
	.unit {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 11px;
		color: rgb(var(--fg-subtle));
	}
	.unit {
		font-size: 10px;
	}
	.spacer {
		flex: 1;
	}
	.range-input {
		width: 56px;
		height: 24px;
		border-radius: 4px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
		font-size: 11px;
		font-family: 'IBM Plex Mono', monospace;
		padding: 0 6px;
		text-align: center;
	}
	.tabular {
		font-variant-numeric: tabular-nums;
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
	.editor {
		border-radius: 8px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--canvas));
		box-shadow: var(--shadow-well);
		padding: 12px;
	}
</style>
