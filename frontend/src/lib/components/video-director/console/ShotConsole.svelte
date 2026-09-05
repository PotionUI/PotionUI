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
	import type { DirectorRunState } from '$lib/types/tabs';
	import {
		normalizeDirectorValue,
		toModelessDirectorValue,
		deriveDirectorMode,
		applyDirectorOperations,
		isChainEdgeKeyframeId,
		resolveDirectorTimingProfile
	} from '$lib/utils/videoDirector';
	import { resolvePromptSegments } from '$lib/utils/promptSegments';
	import { deriveConsoleModel, type ConsoleHeader as ConsoleHeaderModel } from './consoleModel';
	import { deriveShotRail, railTimeFromFraction } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';
	import {
		deriveRailModel,
		withChainKeyframeAt,
		withTimelineKeyframeAt,
		resizeTimelineBlockEdge,
		withTimelineSegmentEdge,
		isKeyframeLocked,
		chainFilmSecondsFromLocal
	} from '../stage-rail/railModel';
	import { withAddedShot, withDuplicatedShot, withRemovedShot, withAddedAudio, withSeamKind, withRemoveKeyframe } from '../stage-rail/stageModel';
	import { mintId, clamp } from '../timelineCore';
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
		selectedVariant = null,
		selectedMode = null,
		formData,
		runs,
		onChange,
		onHeaderChange,
		onCheckedChange,
		onGenerateShots
	}: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		/** Still threaded down to ShotStage (LoRA/IC-LoRA picker scoping) --
		 * NOT used for the header any more (maintainer ruling: no preset chip
		 * in the console). ALSO part of the shot input identity's
		 * `generationContext` below (a preset switch is required scope, not an
		 * optional nicety -- see directorInputIdentity.ts's
		 * `DirectorGenerationContext`). */
		presetId: string;
		/** `Tab.selectedVariant`/`Tab.selectedMode` -- the rest of
		 * `generationContext`, alongside `presetId`. Optional/nullable because
		 * not every caller has them handy yet; `null` still participates in the
		 * identity (see `DirectorGenerationContext`'s doc comment). */
		selectedVariant?: string | null;
		selectedMode?: string | null;
		formData: Record<string, unknown> | null | undefined;
		/** `Tab.directorRuns` (PLAN.md §C W3) -- per-shot generation state, keyed
		 * by shot id. Read-only here (ShotConsole never writes it; +page.svelte
		 * owns the submission that produces it). */
		runs?: Record<string, DirectorRunState>;
		onChange: (v: VideoDirectorValue) => void;
		/** Mirrors the derived header (shot count/duration, readiness,
		 * capability chips) up to VideoDirectorEditor.svelte, which renders it
		 * inside its own `<header>` -- the console itself has no header row any
		 * more (maintainer ruling 09-04: "I don't want to have two headers").
		 * ShotConsole stays the single place `deriveConsoleModel` runs against
		 * the live `doc` (never re-derived from `value` a second time
		 * elsewhere, which could observe a different, in-flight document). */
		onHeaderChange?: (header: ConsoleHeaderModel) => void;
		/** Mirrors the console's own transient row-checkbox selection up to
		 * +page.svelte (same flow as `onHeaderChange`, PLAN.md §C W3) -- the
		 * page's Generate control reads this to scope its submission; the
		 * console itself never gains a Generate control of its own. */
		onCheckedChange?: (checked: Set<string>) => void;
		/** Submits exactly the given shot ids (in the order given) -- backs
		 * this console's two contextual generate actions: a failed row's Retry
		 * (one id) and a broken join's "Generate previous + this shot" (the
		 * contiguous span). Never the console's own bulk Generate control,
		 * which per the maintainer ruling doesn't exist -- +page.svelte owns
		 * the actual submission/tab-state work either way. */
		onGenerateShots?: (shotIds: string[]) => void;
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

	let generationContext = $derived({ presetId: presetId || null, variant: selectedVariant, mode: selectedMode });
	let model = $derived(deriveConsoleModel(doc, capabilities, { activeShotId }, formData, runs, checked, generationContext));
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
	// A chain shot IS its prompt (one full-span beat, PLAN.md D3) -- with
	// nothing selected in the active shot yet (freshly expanded, or a
	// selection belonging to a DIFFERENT shot that's now inactive) it must
	// default to that beat, never the "this shot uses the global prompt"
	// fallback (that fallback is legitimate only for a timeline shot resting
	// between beats, per the 09-04 maintainer bug report). Timeline routing
	// gets no default -- nothing selected there is a real, meaningful state.
	$effect(() => {
		if (!effectiveActiveShotId) return;
		if (selection && selection.shotId === effectiveActiveShotId) return;
		if (capabilities.segmentRouting) selection = { shotId: effectiveActiveShotId, kind: 'beat', id: effectiveActiveShotId };
	});
	// Mirrors the derived header up to VideoDirectorEditor.svelte's own
	// `<header>` -- see this prop's own doc comment above.
	$effect(() => {
		onHeaderChange?.(model.header);
	});
	// Mirrors the checked set up to +page.svelte -- see this prop's own doc
	// comment above.
	$effect(() => {
		onCheckedChange?.(checked);
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

	/** Inserts a new timed prompt beat at `atSeconds` on the named shot,
	 * clamped into the open gap around it (never overlapping a neighbour) --
	 * the position-aware counterpart to stageModel.ts's `withAddedShot`
	 * (which always appends at the end, ignoring position; that's still what
	 * the trailing "+" column's "next open slot" default uses, but a click on
	 * the lane itself, or a drag, needs a real time). */
	function insertTimelineBeatAt(target: VideoDirectorValue, shotId: string, atSeconds: number): VideoDirectorValue {
		const shotIdx = target.timeline.shots.findIndex((s) => s.id === shotId);
		if (shotIdx === -1) return target;
		const shot = target.timeline.shots[shotIdx];
		const segments = shot.segments;
		const sorted = [...segments].sort((a, b) => a.start - b.start);
		const duration = shot.duration;
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
		const nextShots = target.timeline.shots.map((s, i) => (i === shotIdx ? { ...s, segments: [...segments, seg] } : s));
		return { ...target, timeline: { ...target.timeline, shots: nextShots } };
	}

	function handleAddBeat(shotId: string, atSeconds: number) {
		if (capabilities.segmentRouting) return; // one full-span beat per chain shot, no per-beat add (PLAN.md D3)
		doc = insertTimelineBeatAt(doc, shotId, atSeconds);
	}

	function handleAddKeyframe(shotId: string, atSeconds: number) {
		if (capabilities.segmentRouting) {
			const rail = deriveRailModel(doc, capabilities, undefined, resolveDirectorTimingProfile(capabilities, formData, doc));
			const blockIndex = rail.shots.findIndex((s) => s.id === shotId);
			if (blockIndex === -1) return;
			const at = chainFilmSecondsFromLocal(rail, blockIndex, atSeconds);
			const kf = { id: mintId('ckf', doc.chain.keyframes), at, strength: 1, media: null };
			doc = { ...doc, chain: { ...doc.chain, keyframes: [...doc.chain.keyframes, kf] } };
		} else {
			const shotIdx = doc.timeline.shots.findIndex((s) => s.id === shotId);
			if (shotIdx === -1) return;
			const shot = doc.timeline.shots[shotIdx];
			const kf: DirectorKeyframe = { id: mintId('kf', shot.keyframes), start: atSeconds, role: 'free', strength: 1, media: null };
			const nextShots = doc.timeline.shots.map((s, i) => (i === shotIdx ? { ...s, keyframes: [...s.keyframes, kf] } : s));
			doc = { ...doc, timeline: { ...doc.timeline, shots: nextShots } };
		}
	}

	function handleMoveKeyframe(shotId: string, id: string, atSeconds: number) {
		if (capabilities.segmentRouting) {
			if (isChainEdgeKeyframeId(id)) return; // locked well mirrors never move via drag
			const rail = deriveRailModel(doc, capabilities, undefined, resolveDirectorTimingProfile(capabilities, formData, doc));
			const blockIndex = rail.shots.findIndex((s) => s.id === shotId);
			if (blockIndex === -1) return;
			doc = withChainKeyframeAt(doc, id, chainFilmSecondsFromLocal(rail, blockIndex, atSeconds));
		} else {
			const kf = doc.timeline.shots.find((s) => s.id === shotId)?.keyframes.find((k) => k.id === id);
			if (!kf || isKeyframeLocked(kf.role)) return;
			doc = withTimelineKeyframeAt(doc, shotId, id, atSeconds);
		}
	}

	function handleRemoveKeyframe(shotId: string, id: string) {
		if (capabilities.segmentRouting) {
			if (isChainEdgeKeyframeId(id)) return; // locked well mirrors aren't removable from the lane
		} else {
			const kf = doc.timeline.shots.find((s) => s.id === shotId)?.keyframes.find((k) => k.id === id);
			if (!kf || isKeyframeLocked(kf.role)) return;
		}
		doc = withRemoveKeyframe(doc, capabilities, shotId, id);
	}

	function handleAddAudio(shotId: string) {
		doc = withAddedAudio(doc, capabilities, shotId);
	}

	function handleResizeBeat(shotId: string, id: string, edge: 'start' | 'end', atSeconds: number) {
		if (capabilities.segmentRouting) return; // a chain shot's one full-span beat has no independent edges
		const shot = doc.timeline.shots.find((s) => s.id === shotId);
		if (!shot) return;
		const clamped = resizeTimelineBlockEdge(shot.segments, id, edge, atSeconds, shot.duration);
		doc = withTimelineSegmentEdge(doc, shotId, id, edge, clamped);
	}

	function handleSetJoin(afterShotId: string, kind: 'continue' | 'cut') {
		if (capabilities.segmentRouting) {
			const rail = deriveRailModel(doc, capabilities);
			const idx = rail.shots.findIndex((s) => s.id === afterShotId);
			const seam = idx === -1 ? undefined : rail.seams[idx];
			if (!seam) return;
			doc = withSeamKind(doc, capabilities, seam.id, kind);
			return;
		}
		// LTX has no native continuation -- the toggle just sets the NEXT
		// shot's own `continue_from_previous` (PLAN.md's LTX join ruling).
		const shots = doc.timeline.shots;
		const idx = shots.findIndex((s) => s.id === afterShotId);
		const nextShot = idx === -1 ? undefined : shots[idx + 1];
		if (!nextShot) return;
		doc = {
			...doc,
			timeline: {
				...doc.timeline,
				shots: shots.map((s) => (s.id === nextShot.id ? { ...s, continue_from_previous: kind === 'continue' } : s))
			}
		};
	}

	function handleDuplicate(shotId: string) {
		doc = withDuplicatedShot(doc, capabilities, shotId);
	}

	function handleRemove(shotId: string) {
		doc = withRemovedShot(doc, capabilities, shotId);
		if (selection?.shotId === shotId) selection = null;
	}

	function handleAddShot() {
		doc = withAddedShot(doc, capabilities);
	}

	// ─── Contextual generate actions (W3) ────────────────────────────────────
	// Both delegate the actual submission to +page.svelte via `onGenerateShots`
	// -- this component never talks to the generation API itself.
	function handleRetry(shotId: string) {
		onGenerateShots?.([shotId]);
	}
	function handleGeneratePreviousAndThis(spanShotIds: string[]) {
		onGenerateShots?.(spanShotIds);
	}
	function handleConvertToFreshCut(afterShotId: string) {
		handleSetJoin(afterShotId, 'cut');
	}
</script>

<div class="flex flex-col gap-3.5">
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

	{#if checked.size > 0}
		<!-- Maintainer ruling (09-04): there is no "Generate n selected" here --
			the page's own Generate control always decides about the generation.
			Checking a row only scopes what that control submits (a later wave);
			this is purely a transient readout of what's currently checked. -->
		<div class="flex items-center justify-end gap-2.5">
			<span class="font-mono text-[10.5px] tabular-nums text-fg-subtle">{checked.size} selected</span>
			<button
				type="button"
				class="border-none bg-none p-0 text-xs text-fg-subtle underline decoration-line-strong hover:text-fg"
				onclick={clearChecked}
			>
				Clear
			</button>
		</div>
	{/if}

	<div class="flex flex-col">
		{#each model.shots as shot, i (shot.id)}
			{#if shot.id === effectiveActiveShotId}
				<ShotCard
					{shot}
					checked={checked.has(shot.id)}
					onToggleChecked={toggleChecked}
					onDuplicate={handleDuplicate}
					onRemove={handleRemove}
					onRetry={handleRetry}
				>
					<ShotRail
						shotId={shot.id}
						rail={deriveShotRail(doc, capabilities, shot.id, formData)}
						{selection}
						onSelect={(sel) => (selection = sel)}
						onAddBeat={(atSeconds) => handleAddBeat(shot.id, atSeconds)}
						onAddKeyframe={(atSeconds) => handleAddKeyframe(shot.id, atSeconds)}
						onAddAudio={() => handleAddAudio(shot.id)}
						onMoveKeyframe={(id, atSeconds) => handleMoveKeyframe(shot.id, id, atSeconds)}
						onRemoveKeyframe={(id) => handleRemoveKeyframe(shot.id, id)}
						onResizeBeat={(id, edge, atSeconds) => handleResizeBeat(shot.id, id, edge, atSeconds)}
					/>
					<ShotStage {shot} {doc} caps={capabilities} {formData} {presetId} {selection} onDoc={updateDoc} />
					{#if capabilities.segmentRouting}
						<OverridesDisclosure {doc} caps={capabilities} shotId={shot.id} onDoc={updateDoc} />
					{/if}
				</ShotCard>
			{:else}
				<ShotRow
					{shot}
					checked={checked.has(shot.id)}
					onToggleChecked={toggleChecked}
					onActivate={activateShot}
					onRetry={handleRetry}
				/>
			{/if}
			{#if i < model.shots.length - 1}
				{@const join = model.joins.find((j) => j.afterShotId === shot.id && j.beforeShotId === model.shots[i + 1].id)}
				{#if join}
					<JoinConnector
						{join}
						onSetJoin={handleSetJoin}
						onGeneratePreviousAndThis={handleGeneratePreviousAndThis}
						onConvertToFreshCut={handleConvertToFreshCut}
					/>
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
