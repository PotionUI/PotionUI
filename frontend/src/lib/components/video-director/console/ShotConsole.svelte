<script lang="ts">
	// The Shot Console (W1): replaces the old Stage+Rail composition as the
	// whole Video Director surface. Owns the document itself (moved verbatim
	// from VideoDirectorEditor.svelte -- the `lastEmitted` re-entrancy guard
	// idiom is unchanged, see that file's former lines 33-83), plus the
	// console-only state that has no document field: which shot is expanded
	// (`activeShotId`), the current rail selection (`selection`, COMPONENT
	// state per PLAN.md -- never a module-scope store, see the memory trap on
	// `railSelection.ts`), and the per-shot "generate this" checkbox set
	// (`checked`, transient -- W3 wires a real run queue against it).
	//
	// Composes ConsoleHeader, two FilmPromptRow rows, the shot stack
	// (ShotRow collapsed / ShotCard+ShotRail+ShotStage expanded), a
	// JoinConnector between consecutive chain shots, and a trailing "Add
	// shot" row -- the last has no template anatomy (PLAN.md D7 only rules on
	// the empty-film case), so its markup here is this file's own, kept to
	// existing semantic tokens.
	import { untrack } from 'svelte';
	import type { VideoDirectorValue, DirectorCapabilities, DirectorKeyframe, DirectorPromptSegment } from '$lib/types/videoDirector';
	import type { Segment } from '$lib/types/segments';
	import {
		normalizeDirectorValue,
		toModelessDirectorValue,
		deriveDirectorMode,
		applyDirectorOperations,
		isChainEdgeKeyframeId
	} from '$lib/utils/videoDirector';
	import { resolvePromptSegments } from '$lib/utils/promptSegments';
	import { deriveConsoleModel } from './consoleModel';
	import { deriveShotRail, railTimeFromFraction } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';
	import {
		deriveRailModel,
		withChainKeyframeAt,
		withTimelineKeyframeAt,
		resizeTimelineBlockEdge,
		withTimelineSegmentEdge,
		isKeyframeLocked,
		type RailModel
	} from '../stage-rail/railModel';
	import { withAddedShot, withDuplicatedShot, withAddedAudio, withSeamKind } from '../stage-rail/stageModel';
	import { mintId, clamp } from '../timelineCore';
	import ConsoleHeader from './ConsoleHeader.svelte';
	import FilmPromptRow from './FilmPromptRow.svelte';
	import ShotRow from './ShotRow.svelte';
	import ShotCard from './ShotCard.svelte';
	import ShotRail from './ShotRail.svelte';
	import ShotStage from './ShotStage.svelte';
	import JoinConnector from './JoinConnector.svelte';
	import OverridesDisclosure from './OverridesDisclosure.svelte';

	let {
		value,
		capabilities,
		presetId,
		formData,
		onChange
	}: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		/** Still threaded down to ShotStage (LoRA/IC-LoRA picker scoping) --
		 * NOT used for the header any more (maintainer ruling: no preset chip
		 * in the console). */
		presetId: string;
		formData: Record<string, unknown> | null | undefined;
		onChange: (v: VideoDirectorValue) => void;
	} = $props();

	function project(raw: unknown): VideoDirectorValue {
		return toModelessDirectorValue(normalizeDirectorValue(raw, capabilities), capabilities);
	}

	let doc = $state(project(value));
	let lastEmitted: VideoDirectorValue = untrack(() => doc);

	$effect(() => {
		if (lastEmitted && JSON.stringify(value) === JSON.stringify(lastEmitted)) return;
		const next = project(value);
		doc = next;
		lastEmitted = next;
	});

	$effect(() => {
		const derivedMode = deriveDirectorMode(doc, capabilities);
		if (doc.mode !== derivedMode) {
			doc = { ...doc, mode: derivedMode };
			return;
		}
		if (JSON.stringify(doc) === JSON.stringify(lastEmitted)) return;
		lastEmitted = doc;
		onChange(doc);
	});

	function updateDoc(next: VideoDirectorValue) {
		doc = next;
	}

	// ─── Console-only state ────────────────────────────────────────────────
	let activeShotId: string | null = $state(null);
	let selection: ConsoleSelection = $state(null);
	let checked: Set<string> = $state(new Set());

	let model = $derived(deriveConsoleModel(doc, capabilities, { activeShotId }, formData));
	// The default active shot is the first one; a stale id (its shot got
	// removed) resolves back to the first shot too -- never a dangling
	// expansion. Reading this everywhere instead of the raw `activeShotId`
	// state avoids a one-tick flash on mount where nothing is expanded yet.
	let effectiveActiveShotId = $derived(
		model.shots.some((s) => s.id === activeShotId) ? activeShotId : (model.shots[0]?.id ?? null)
	);
	$effect(() => {
		if (effectiveActiveShotId !== activeShotId) activeShotId = effectiveActiveShotId;
	});
	// A selection never survives its shot disappearing (removed shot) --
	// mirrors `consoleSelectionBelongsToShot`'s own contract.
	$effect(() => {
		if (selection && !model.shots.some((s) => s.id === selection!.shotId)) selection = null;
	});

	function activateShot(shotId: string) {
		activeShotId = shotId;
	}

	function toggleChecked(shotId: string) {
		const next = new Set(checked);
		if (next.has(shotId)) next.delete(shotId);
		else next.add(shotId);
		checked = next;
	}
	function clearChecked() {
		checked = new Set();
	}

	// ─── Film-level prompt segment writers ──────────────────────────────────
	// Mirrors stageModel.ts's `withShotPromptSegments` idiom -- there is no
	// existing setter for a rich `Segment[]` write on the FILM-level global/
	// negative prompt (only `applySetPrompt`/`applySetNegativePrompt`'s
	// flattened-single-segment-from-plain-text path, which the old Stage.svelte
	// row used); FilmPromptRow.svelte's real SegmentedPromptEditor needs the
	// richer write, same field, same flattened-text mirror.
	function withGlobalPromptSegments(target: VideoDirectorValue, segments: Segment[]): VideoDirectorValue {
		return { ...target, global_prompt_segments: segments, global_prompt: resolvePromptSegments(segments) };
	}
	function withNegativePromptSegments(target: VideoDirectorValue, segments: Segment[]): VideoDirectorValue {
		return { ...target, negative_prompt_segments: segments, negative_prompt: resolvePromptSegments(segments) };
	}

	// ─── Rail interaction wiring ─────────────────────────────────────────────
	// The rail's hover insert-cue / trailing "+" column are NEW W1 interaction
	// surface (no film-wide zoom/px rail existed per-shot before); the actual
	// document writes below still go through the same idiom every other
	// `with*` setter in this feature uses (immutable, mirrors an existing
	// field), never a forked shape.

	/** Converts a chain shot's LOCAL time (0..segment.duration, i.e. that
	 * shot's own generation window, INCLUDING any leading overlap it inherits
	 * -- see shotRailModel.ts's header note) back to the chain's FILM/output
	 * time `chain.keyframes[].at` is stored in. Inverse of
	 * `chainLandingShotIndex` + the local-frame math in shotRailModel.ts. */
	function chainFilmSecondsFromLocal(rail: RailModel, blockIndex: number, localSeconds: number): number {
		const block = rail.shots[blockIndex];
		const fps = rail.fps;
		const localFrame = Math.round(localSeconds * fps);
		const outputFrame = Math.max(0, localFrame - block.overlapInFrames);
		return block.startSeconds + (fps > 0 ? outputFrame / fps : 0);
	}

	/** Inserts a new timed prompt beat at `atSeconds`, clamped into the open
	 * gap around it (never overlapping a neighbour) -- the position-aware
	 * counterpart to stageModel.ts's `withAddedShot` (which always appends at
	 * the end, ignoring position; that's still what the trailing "+" column's
	 * "next open slot" default uses, but a click on the lane itself, or a
	 * drag, needs a real time). */
	function insertTimelineBeatAt(target: VideoDirectorValue, atSeconds: number): VideoDirectorValue {
		const segments = target.timeline.segments;
		const sorted = [...segments].sort((a, b) => a.start - b.start);
		const duration = target.timeline.duration;
		let leftBound = 0;
		let rightBound = duration;
		for (const s of sorted) {
			if (s.end <= atSeconds) leftBound = Math.max(leftBound, s.end);
			if (s.start >= atSeconds) {
				rightBound = Math.min(rightBound, s.start);
				break;
			}
		}
		const start = clamp(atSeconds, leftBound, rightBound);
		const end = Math.min(rightBound, start + Math.max(0.25, Math.min(1, rightBound - leftBound)));
		if (end - start < 0.1) return target; // no open room here
		const seg: DirectorPromptSegment = { id: mintId('seg', segments), start, end, text: '', prompt_segments: [] };
		return { ...target, timeline: { ...target.timeline, segments: [...segments, seg] } };
	}

	function handleAddBeat(shotId: string, atSeconds: number) {
		if (capabilities.segmentRouting) return; // one full-span beat per chain shot, no per-beat add (PLAN.md D3)
		doc = insertTimelineBeatAt(doc, atSeconds);
	}

	function handleAddKeyframe(shotId: string, atSeconds: number) {
		const rail = deriveRailModel(doc, capabilities);
		if (capabilities.segmentRouting) {
			const blockIndex = rail.shots.findIndex((s) => s.id === shotId);
			if (blockIndex === -1) return;
			const at = chainFilmSecondsFromLocal(rail, blockIndex, atSeconds);
			const kf = { id: mintId('ckf', doc.chain.keyframes), at, strength: 1, media: null };
			doc = { ...doc, chain: { ...doc.chain, keyframes: [...doc.chain.keyframes, kf] } };
		} else {
			const kf: DirectorKeyframe = { id: mintId('kf', doc.timeline.keyframes), start: atSeconds, role: 'free', strength: 1, media: null };
			doc = { ...doc, timeline: { ...doc.timeline, keyframes: [...doc.timeline.keyframes, kf] } };
		}
	}

	function handleMoveKeyframe(shotId: string, id: string, atSeconds: number) {
		const rail = deriveRailModel(doc, capabilities);
		if (capabilities.segmentRouting) {
			if (isChainEdgeKeyframeId(id)) return; // locked well mirrors never move via drag
			const blockIndex = rail.shots.findIndex((s) => s.id === shotId);
			if (blockIndex === -1) return;
			doc = withChainKeyframeAt(doc, id, chainFilmSecondsFromLocal(rail, blockIndex, atSeconds));
		} else {
			const kf = doc.timeline.keyframes.find((k) => k.id === id);
			if (!kf || isKeyframeLocked(kf.role)) return;
			doc = withTimelineKeyframeAt(doc, id, atSeconds);
		}
	}

	function handleAddAudio() {
		// Chain and timeline audio are both document/film-wide tracks (see
		// `chain.audio`/`timeline.audio`'s own doc comments) -- adding "for
		// this shot" is really adding to the shared list; shotRailModel.ts
		// already clips/rebases whichever shots a track's span touches.
		doc = withAddedAudio(doc, capabilities);
	}

	function handleResizeBeat(shotId: string, id: string, edge: 'start' | 'end', atSeconds: number) {
		if (capabilities.segmentRouting) return; // a chain shot's one full-span beat has no independent edges
		const clamped = resizeTimelineBlockEdge(doc.timeline.segments, id, edge, atSeconds, doc.timeline.duration);
		doc = withTimelineSegmentEdge(doc, id, edge, clamped);
	}

	function handleSetJoin(afterShotId: string, kind: 'continue' | 'cut') {
		const rail = deriveRailModel(doc, capabilities);
		const idx = rail.shots.findIndex((s) => s.id === afterShotId);
		const seam = idx === -1 ? undefined : rail.seams[idx];
		if (!seam) return;
		doc = withSeamKind(doc, capabilities, seam.id, kind);
	}

	function handleDuplicate(shotId: string) {
		if (!capabilities.segmentRouting) return;
		doc = withDuplicatedShot(doc, capabilities, shotId);
	}

	function handleRemove(shotId: string) {
		if (!capabilities.segmentRouting) return;
		doc = applyDirectorOperations(doc, [{ op: 'remove_segment', id: shotId }], capabilities);
		if (selection?.shotId === shotId) selection = null;
	}

	function handleAddShot() {
		doc = withAddedShot(doc, capabilities);
	}
</script>

<div class="flex flex-col gap-3.5">
	<ConsoleHeader
		header={model.header}
		checkedCount={checked.size}
		queuedCount={0}
		onGenerateSelected={() => {}}
		onClearChecked={clearChecked}
	/>

	<div>
		<FilmPromptRow
			row={model.filmRows[0]}
			position="first"
			segments={doc.global_prompt_segments}
			onSegmentsChange={(segments) => (doc = withGlobalPromptSegments(doc, segments))}
		/>
		<FilmPromptRow
			row={model.filmRows[1]}
			position="last"
			segments={doc.negative_prompt_segments}
			onSegmentsChange={(segments) => (doc = withNegativePromptSegments(doc, segments))}
		/>
	</div>

	<div class="flex flex-col">
		{#each model.shots as shot, i (shot.id)}
			{#if shot.id === effectiveActiveShotId}
				<ShotCard
					{shot}
					checked={checked.has(shot.id)}
					onToggleChecked={toggleChecked}
					onDuplicate={handleDuplicate}
					onRemove={handleRemove}
				>
					<ShotRail
						shotId={shot.id}
						rail={deriveShotRail(doc, capabilities, shot.id, formData)}
						{selection}
						onSelect={(sel) => (selection = sel)}
						onAddBeat={(atSeconds) => handleAddBeat(shot.id, atSeconds)}
						onAddKeyframe={(atSeconds) => handleAddKeyframe(shot.id, atSeconds)}
						onAddAudio={handleAddAudio}
						onMoveKeyframe={(id, atSeconds) => handleMoveKeyframe(shot.id, id, atSeconds)}
						onResizeBeat={(id, edge, atSeconds) => handleResizeBeat(shot.id, id, edge, atSeconds)}
					/>
					<ShotStage {shot} {doc} caps={capabilities} {formData} {presetId} {selection} onDoc={updateDoc} />
					{#if capabilities.segmentRouting}
						<OverridesDisclosure />
					{/if}
				</ShotCard>
			{:else}
				<ShotRow {shot} checked={checked.has(shot.id)} onToggleChecked={toggleChecked} onActivate={activateShot} />
			{/if}
			{#if i < model.shots.length - 1}
				{@const join = model.joins.find((j) => j.afterShotId === shot.id && j.beforeShotId === model.shots[i + 1].id)}
				{#if join}
					<JoinConnector {join} onSetJoin={handleSetJoin} />
				{/if}
			{/if}
		{/each}

		<button
			type="button"
			class="add-shot-row"
			disabled={!model.canAddShot}
			title={!model.canAddShot ? (model.addShotDisabledReason ?? undefined) : undefined}
			onclick={handleAddShot}
		>
			+ Add shot
		</button>
	</div>
</div>

<style>
	/* No template anatomy for this row (PLAN.md D7 only specifies the
	   empty-film case) -- kept to the same dashed/disabled idiom the rail's
	   own add buttons and RailPromptLane's global-fill use elsewhere in this
	   feature, semantic tokens only. */
	.add-shot-row {
		margin-top: 10px;
		height: 40px;
		border-radius: 6px;
		border: 1px dashed rgb(var(--line-strong));
		background: transparent;
		color: rgb(var(--fg-subtle));
		font-size: 12px;
		cursor: pointer;
	}
	.add-shot-row:hover:not(:disabled) {
		color: rgb(var(--fg));
		border-color: rgb(var(--line-hover));
		background: rgb(var(--surface-1));
	}
	.add-shot-row:disabled {
		cursor: not-allowed;
		opacity: 0.4;
	}
</style>
