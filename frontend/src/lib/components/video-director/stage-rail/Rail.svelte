<script lang="ts">
	// The multi-lane timeline for the Stage & Rail Video Director rework.
	// Renders deriveRailModel(doc, caps) and owns selection (railSelection
	// store); editing values (prompts, media, per-shot facts) is Stage
	// territory -- this component selects objects, repositions timed items,
	// appends blank shots/keyframes/audio, and owns the fps/duration timing
	// base (the one place those are edited now that the old per-composer
	// toolbars are gone).
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import {
		deriveRailModel,
		resizeTimelineBlockEdge,
		withChainKeyframeAt,
		withTimelineKeyframeAt,
		withTimelineSegmentEdge
	} from './railModel';
	import { withAddedShot, withAddedKeyframe, withAddedAudio } from './stageModel';
	import { evaluateDirectorTiming, collectFormMediaOptions, formMediaOptionKeys } from '$lib/utils/videoDirector';
	import { selectRailObject, isRailObjectSelected, railSelection } from './railSelection';
	import { DEFAULT_ZOOM, MIN_ZOOM, MAX_ZOOM, stepZoom, totalWidth, RULER_H } from '../timelineCore';
	import RailRuler from './RailRuler.svelte';
	import ShotsLane from './ShotsLane.svelte';
	import KeyframesLane from './KeyframesLane.svelte';
	import AudioLane from './AudioLane.svelte';
	import { IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	const GUTTER_W = 84;
	const SHOTS_LANE_H = 56;
	const SUB_LANE_H = 30;
	const ROW_GAP = 8;

	let {
		doc = $bindable(),
		caps,
		formData
	}: {
		doc: VideoDirectorValue;
		caps: DirectorCapabilities;
		formData?: Record<string, unknown> | null;
	} = $props();

	let zoom = $state(DEFAULT_ZOOM);
	let model = $derived(deriveRailModel(doc, caps));
	let contentWidth = $derived(totalWidth(model.totalSeconds, zoom));

	// The whole-film reference pool, read from the form fields the mode
	// declares -- a read-model only. Editing the pool happens on the form's
	// own References tab; this strip links attention to it, it doesn't edit it.
	let referencePool = $derived(
		model.lanes.references ? collectFormMediaOptions(formData).filter((o) => model.referenceFields.includes(o.field)) : []
	);
	let referencePoolPreview = $derived(referencePool.slice(0, 6));
	let referencePoolPreviewKeys = $derived(formMediaOptionKeys(referencePoolPreview));

	let summary = $derived(
		[
			`${model.totalFrames} f`,
			`${model.totalSeconds.toFixed(2)} s`,
			`${model.shots.length} shot${model.shots.length === 1 ? '' : 's'}`,
			...(model.lanes.keyframes ? [`${model.keyframes.length} keyframe${model.keyframes.length === 1 ? '' : 's'}`] : []),
			...(model.lanes.audio && model.audio.length > 0 ? [`${model.audio.length} audio`] : [])
		].join(' · ')
	);

	function handleMoveKeyframe(id: string, atSeconds: number) {
		doc = model.routing === 'chain' ? withChainKeyframeAt(doc, id, atSeconds) : withTimelineKeyframeAt(doc, id, atSeconds);
	}

	function handleResizeEdge(id: string, edge: 'start' | 'end', proposedSeconds: number) {
		const clamped = resizeTimelineBlockEdge(doc.timeline.segments, id, edge, proposedSeconds, doc.timeline.duration);
		doc = withTimelineSegmentEdge(doc, id, edge, clamped);
	}

	function addShot() {
		doc = withAddedShot(doc, caps);
	}
	function addKeyframe() {
		doc = withAddedKeyframe(doc, caps);
	}
	function addAudio() {
		doc = withAddedAudio(doc, caps);
	}

	// Timing base (fps -- both routings; whole-video duration -- timeline
	// routing only, since a chain's total is derived from its per-shot
	// durations, not stored). Chain's own duration/frames is the summary
	// strip above; this is the one place fps/duration are actually editable
	// now that the old per-composer toolbars (ChainSettingsTab,
	// KeyframeTimeline) are gone.
	let timing = $derived(
		model.routing === 'timeline'
			? evaluateDirectorTiming(doc.timeline.duration, doc.timeline.fps, { maxDuration: caps.maxDuration, maxFrames: caps.maxFrames })
			: evaluateDirectorTiming(1, doc.chain.fps, { maxDuration: null, maxFrames: null })
	);
	function setFps(raw: string) {
		if (raw === '') return;
		const fps = Math.round(Number(raw));
		if (!Number.isFinite(fps)) return;
		doc = model.routing === 'chain' ? { ...doc, chain: { ...doc.chain, fps } } : { ...doc, timeline: { ...doc.timeline, fps } };
	}
	function setDuration(raw: string) {
		if (raw === '' || model.routing !== 'timeline') return;
		const duration = Number(raw);
		if (!Number.isFinite(duration)) return;
		doc = { ...doc, timeline: { ...doc.timeline, duration } };
	}
</script>

<div class="rounded-lg border border-line bg-surface-1 shadow-floating">
	<div class="flex flex-wrap items-center gap-3 px-4 py-3.5 mb-3">
		<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Rail</span>
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">{summary}</span>
		<label class="flex items-center gap-1.5 font-mono text-2xs text-fg-subtle">
			<span class="uppercase tracking-[0.06em]">FPS</span>
			<input
				type="number"
				min="1"
				max="60"
				step="1"
				class="input w-14 py-1 text-right text-xs tabular-nums {timing.fieldErrors.fps ? 'border-danger' : ''}"
				value={model.fps}
				aria-invalid={!!timing.fieldErrors.fps}
				oninput={(e) => setFps((e.currentTarget as HTMLInputElement).value)}
			/>
		</label>
		{#if model.routing === 'timeline'}
			<label class="flex items-center gap-1.5 font-mono text-2xs text-fg-subtle">
				<span class="uppercase tracking-[0.06em]">Duration</span>
				<input
					type="number"
					min="0.1"
					step="0.5"
					class="input w-16 py-1 text-right text-xs tabular-nums {timing.fieldErrors.duration ? 'border-danger' : ''}"
					value={doc.timeline.duration}
					aria-invalid={!!timing.fieldErrors.duration}
					oninput={(e) => setDuration((e.currentTarget as HTMLInputElement).value)}
				/>
				<span>s</span>
			</label>
		{/if}
		<div class="flex-1"></div>
		<div class="flex items-center gap-0.5 rounded bg-surface-2 p-0.5 ring-1 ring-inset ring-line">
			<IconButton icon="minus" label="Zoom out" size="sm" disabled={zoom <= MIN_ZOOM} onclick={() => (zoom = stepZoom(zoom, -1))} />
			<IconButton icon="plus" label="Zoom in" size="sm" disabled={zoom >= MAX_ZOOM} onclick={() => (zoom = stepZoom(zoom, 1))} />
		</div>
	</div>
	{#if timing.fieldErrors.fps}
		<p class="px-4 font-mono text-2xs text-danger">{timing.fieldErrors.fps}</p>
	{/if}
	{#if timing.fieldErrors.duration}
		<p class="px-4 font-mono text-2xs text-danger">{timing.fieldErrors.duration}</p>
	{/if}

	<div class="overflow-x-auto px-4 pb-3">
		<div class="flex gap-3" style="min-width: {GUTTER_W + contentWidth}px">
			<div class="flex shrink-0 flex-col" style="width: {GUTTER_W}px; gap: {ROW_GAP}px">
				<div style="height: {RULER_H}px"></div>
				{#if model.lanes.references}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Refs</span>
					</div>
				{/if}
				{#if model.lanes.icLora && model.icLora}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Whole video</span>
					</div>
				{/if}
				<div class="flex items-center" style="height: {SHOTS_LANE_H}px">
					<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Shots</span>
				</div>
				{#if model.lanes.keyframes}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Keyframes</span>
					</div>
				{/if}
				{#if model.lanes.audio}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-muted">Audio</span>
					</div>
				{/if}
			</div>

			<div class="relative flex flex-1 flex-col" style="width: {contentWidth}px; gap: {ROW_GAP}px">
				<RailRuler totalSeconds={model.totalSeconds} {zoom} />

				{#if model.lanes.references}
					<div
						class="flex items-center gap-2 rounded bg-canvas px-2.5 ring-1 ring-inset ring-line"
						style="height: {SUB_LANE_H}px"
					>
						<div class="flex -space-x-1.5">
							{#each referencePoolPreview as opt, i (referencePoolPreviewKeys[i])}
								<div class="flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded ring-1 ring-canvas bg-surface-2">
									{#if opt.item.type === 'image' && opt.item.url}
										<img src={opt.item.url} alt="" class="h-full w-full object-cover" />
									{:else}
										<Icon
											name={opt.item.type === 'video' ? 'video' : opt.item.type === 'audio' ? 'audio' : 'image'}
											className="h-2.5 w-2.5 text-fg-subtle"
										/>
									{/if}
								</div>
							{/each}
						</div>
						<span class="font-mono text-2xs tabular-nums text-fg-subtle">{referencePool.length}</span>
						<div class="flex-1"></div>
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">applies to the whole film</span>
					</div>
				{/if}

				{#if model.lanes.icLora && model.icLora}
					{@const icLora = model.icLora}
					{@const selected = isRailObjectSelected($railSelection, 'ic_lora', icLora.id)}
					<button
						type="button"
						class="flex items-center gap-2 rounded px-2.5 text-left transition-colors
							{selected ? 'bg-signal/10 ring-1 ring-inset ring-signal' : 'bg-canvas ring-1 ring-inset ring-line hover:ring-line-hover'}"
						style="height: {SUB_LANE_H}px"
						onclick={() => selectRailObject('ic_lora', icLora.id)}
					>
						<span class="text-xs text-fg">{icLora.hasLora ? 'IC-LoRA set' : 'No IC-LoRA'}</span>
						<span class="font-mono text-2xs text-fg-subtle">{icLora.hasReference ? 'reference attached' : 'no reference'}</span>
						<div class="flex-1"></div>
						<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">applies to every frame</span>
					</button>
				{/if}

				<ShotsLane {model} {zoom} onResizeEdge={model.routing === 'timeline' ? handleResizeEdge : undefined} />

				{#if model.lanes.keyframes}
					<KeyframesLane {model} {zoom} onMove={handleMoveKeyframe} />
				{/if}

				{#if model.lanes.audio}
					<AudioLane {model} {zoom} />
				{/if}
			</div>

			<div class="sticky right-0 z-10 flex shrink-0 flex-col items-center bg-surface-1 pl-2" style="gap: {ROW_GAP}px">
				<div style="height: {RULER_H}px"></div>
				{#if model.lanes.references}
					<div style="height: {SUB_LANE_H}px"></div>
				{/if}
				{#if model.lanes.icLora && model.icLora}
					<div style="height: {SUB_LANE_H}px"></div>
				{/if}
				<div class="flex items-center" style="height: {SHOTS_LANE_H}px">
					{@render addButton('Add shot', SHOTS_LANE_H, !model.canAddShot, addShot)}
				</div>
				{#if model.lanes.keyframes}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						{#if model.freePlacementActive}
							{@render addButton(
								'Add keyframe',
								SUB_LANE_H,
								model.maxKeyframes != null && model.keyframes.length >= model.maxKeyframes,
								addKeyframe
							)}
						{/if}
					</div>
				{/if}
				{#if model.lanes.audio}
					<div class="flex items-center" style="height: {SUB_LANE_H}px">
						{@render addButton('Add audio', SUB_LANE_H, false, addAudio)}
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

{#snippet addButton(label: string, height: number, disabled: boolean, onClick: () => void)}
	<button
		type="button"
		class="flex items-center justify-center rounded bg-canvas ring-1 ring-inset ring-line-strong text-fg-subtle transition-colors hover:text-fg hover:ring-signal disabled:pointer-events-none disabled:opacity-40"
		style="width: 30px; height: {height}px"
		{disabled}
		onclick={onClick}
		aria-label={label}
	>
		+
	</button>
{/snippet}
