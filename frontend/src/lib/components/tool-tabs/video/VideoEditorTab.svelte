<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import {
		CanvasChip,
		EditStack,
		EditorCanvas,
		EditorFrame,
		EditorToolbar,
		FrameSheet,
		OptionsPanel,
		ToolRail
	} from '$lib/components/tool-tabs/frame';
	import SaveMenuRows from '$lib/components/tool-tabs/frame/SaveMenuRows.svelte';
	import ReplaceMenu from '$lib/components/tool-tabs/frame/ReplaceMenu.svelte';
	import { replaceSource, sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import MediaFieldPickers from '$lib/components/form-fields/MediaFieldPickers.svelte';
	import { pickFromGeneration } from '$lib/components/form-fields/mediaLoaderPicks';
	import { buildEditedMediaItem } from '$lib/components/form-fields/mediaLoaderEdited';
	import { fitViewport } from '$lib/tool-tabs/frame/viewport';
	import {
		apply,
		beforeSteps,
		beginEdit,
		canRedo,
		canUndo,
		discardPending,
		effectiveSteps,
		move,
		previewSteps,
		redo,
		redoLabel,
		remove,
		reopen,
		seek,
		toggleHidden,
		undo,
		undoLabel,
		updatePending,
		type EditStackState
	} from '$lib/tool-tabs/frame/editStack';
	import type { SaveAction } from '$lib/tool-tabs/frame/types';
	import type { ToolTabHost } from '$lib/tool-tabs/types';
	import { mediaFileStem } from '$lib/tool-tabs/sources';
	import { CROP_ASPECTS, FULL_CROP, fitAspect, type CropRect } from '$lib/media/editors/cropGeometry';
	import { ASSUMED_FPS, safeFps } from '$lib/media/editors/frameTiming';
	import { resolveEditableResource } from '$lib/media/editors/editorSource';
	import { getCachedSchema } from '$lib/form/schemaCache';
	import { api } from '$lib/services/api/index';
	import {
		cancelMediaToolRun,
		getMediaToolRun,
		listMediaToolRuns,
		effectAnalysis,
		effectShader,
		textureTable,
		previewFrame,
		startClipPreview,
		startMediaToolRun,
		uploadMediaToolArtifact
	} from '$lib/services/api/mediaTools';
	import { isTerminalStatus, type MediaToolInputRef, type MediaToolRun, type MediaToolSaveMode } from '$lib/media-tools/types';
	import type { UploadFileInfo } from '$lib/services/api/media';
	import { toasts } from '$lib/stores/toast';
	import { tabsStore } from '$lib/stores/tabs';
	import { isGenerationTab, isToolTab, type MediaRef } from '$lib/types/tabs';
	import { logger } from '$lib/utils/logger';
	import {
		compileSteps,
		defaultParams,
		draftFor,
		fixed2,
		hasPixelEffects,
		hasSplit,
		isEffectOp,
		isNoop,
		isRailToolId,
		isVideoOp,
		playbackPreview,
		RAIL_GROUPS,
		railToolOf,
		readCrop,
		readSpeed,
		stepsSignature,
		type ClipInfo,
		type RailToolId,
		type VideoOp,
		type VideoParams
	} from '$lib/tool-tabs/video/videoSteps';
	import { keptRange } from '$lib/tool-tabs/video/timeMap';
	import { loadMediaToolCatalog, mediaToolCatalog } from '$lib/media-tools/catalog';
	import { effectGroups, effectTools, type EffectTool } from '$lib/effects/catalog';
	import { planChain, type AnalysisNeed, type ChainPlan } from '$lib/effects/plan';
	import { liveState } from '$lib/effects/liveMode';
	import { PREVIEW_CAPS } from '$lib/effects/budget';
	import { supportsWebGL2 } from '$lib/effects/ShaderChain';
	import LivePreview from './LivePreview.svelte';
	import {
		EMPTY_SIGNATURE,
		directInputRef,
		dirtyCount,
		formatEditorTime,
		readVideoState,
		settleState,
		writeVideoState,
		type LoadedVideoState,
		type SavedCopy
	} from '$lib/tool-tabs/video/videoState';
	import { loadPeaks, runStrip, stripTimes } from '$lib/tool-tabs/video/stripScheduler';
	import { createFrameGrabber } from '$lib/tool-tabs/video/frameGrabber';
	import { abortableWait, findRun, percentOf, secondsLeft, trackRun, type RenderRunApi } from '$lib/tool-tabs/video/renderRun';
	import { detachRender, followRender, isFollowing, settleRender, stopRender, type RenderListener } from '$lib/tool-tabs/video/renderFollow';
	import { requestFrame } from '$lib/tool-tabs/video/frameRequest';
	import { resizeOptions } from '$lib/tool-tabs/video/params';
	import { buildSendTargets, videoFieldsOf, withFieldValue, type VideoField, type VideoSendTarget } from '$lib/tool-tabs/video/sendTargets';
	import ScrubBar from './ScrubBar.svelte';
	import RenderCard from './RenderCard.svelte';
	import VideoToolOptions from './VideoToolOptions.svelte';
	import CropOverlay from './CropOverlay.svelte';

	type OpenHint = { effect?: string; sendTo?: { tabId: string; field: string } } | null | undefined;
	type HintedHost = ToolTabHost & { hint?: OpenHint; clearHint?: () => void };
	type RefWithMeta = MediaRef & { metadata?: { width?: number | null; height?: number | null; duration_seconds?: number | null; fps?: number | null } };

	interface RenderState {
		runId: string;
		percent: number;
		mode: MediaToolSaveMode;
		signature: string;
		startedAt: number;
		total: number;
		output: string;
		cancelling: boolean;
	}

	let { host }: { host: ToolTabHost } = $props();

	const hostApi = untrack(() => host);
	const tabId = hostApi.tabId;
	const initial = readVideoState(untrack(() => host.state));
	const runApi: RenderRunApi = { get: getMediaToolRun, list: listMediaToolRuns };
	const toolLabels: Record<RailToolId, string> = {
		trim: 'Trim',
		split: 'Split',
		crop: 'Crop',
		resize: 'Resize',
		speed: 'Speed',
		mute: 'Mute',
		volume: 'Volume',
		replace: 'Replace audio',
		effects: 'Effects',
		frame: 'Frame'
	};
	const toolIcons: Record<RailToolId, string> = Object.fromEntries(
		RAIL_GROUPS.flatMap((group) => group.tools.map((entry) => [entry.id, entry.icon]))
	) as Record<RailToolId, string>;

	let edits = $state.raw<EditStackState<VideoParams>>(initial.stack);
	let tool = $state<RailToolId>(isRailToolId(untrack(() => host.activeTool)) ? (untrack(() => host.activeTool) as RailToolId) : (initial.tool ?? 'trim'));
	let activeEffect = $state<string | null>(null);
	let split = $state(0.5);
	let webgl2 = $state(false);
	let contextLosses = $state(0);
	let previewCap = $state<number>(PREVIEW_CAPS[0]);
	let unavailable = $state.raw<ReadonlySet<string>>(new Set());
	let shaderTexts = $state.raw<ReadonlyMap<string, string>>(new Map());
	let lutTables = $state.raw<ReadonlyMap<string, Uint16Array>>(new Map());
	const lutLoading = new Set<string>();
	let measured = $state.raw<ReadonlyMap<string, Record<string, number>>>(new Map());
	const shaderLoading = new Set<string>();
	const analysisLoading = new Set<string>();
	const lastMeasured = new Map<string, Record<string, number>>();
	let aspect = $state('free');
	let playhead = $state(initial.playhead);
	let storedPlayhead = $state(initial.playhead);
	let loop = $state(initial.loop);
	let playbackMuted = $state(false);
	let savedSignature = $state(initial.savedSignature);
	let saved = $state.raw<SavedCopy | null>(initial.saved);
	let render = $state<RenderState | null>(null);
	let compare = $state(false);
	let viewport = $state(fitViewport());
	let stackOpen = $state(false);
	let saveMenuOpen = $state(false);
	let saveSheetOpen = $state(false);
	let optionsCollapsed = $state(false);
	let playing = $state(false);
	let video = $state<HTMLVideoElement | null>(null);
	let meta = $state<{ width: number; height: number; duration: number } | null>(null);
	let videoError = $state(false);
	let inputRef = $state.raw<MediaToolInputRef | null>(null);
	let origin = $state<'library' | 'history' | null>(null);
	let refReason = $state<string | null>(null);
	let frames = $state<(string | null)[]>([]);
	let peaks = $state<number[] | null>(null);
	let afterFrame = $state<{ key: string; url: string } | null>(null);
	let beforeFrame = $state<{ key: string; url: string } | null>(null);
	let clipPreview = $state<{ key: string; sig: string; state: 'rendering' | 'ready'; url: string | null; runId: string | null } | null>(null);
	let busy = $state<string | null>(null);
	let pickers = $state<'library' | 'history' | 'audio' | null>(null);
	let fieldsByTab = $state<Record<string, VideoField[]>>({});
	let preferredSend = $state<string | null>(null);
	let audioInput = $state<HTMLInputElement | null>(null);
	let afterRender: ((run: MediaToolRun) => void) | null = null;
	let starting = $state(false);
	let clipController: AbortController | null = null;
	const clipCache = new Map<string, string>();
	let raf = 0;
	let destroyed = false;

	let phone = $derived(host.phone);
	let source = $derived(host.source as RefWithMeta | null);
	let url = $derived(source?.url ?? null);
	let stem = $derived(mediaFileStem(source));
	let fps = $derived(safeFps(source?.metadata?.fps ?? ASSUMED_FPS));
	let ready = $derived(!!meta && meta.duration > 0);
	let clip = $derived<ClipInfo>(meta ?? { width: 1280, height: 720, duration: 0 });
	let duration = $derived(clip.duration);

	let effectList = $derived<EffectTool[]>(effectTools($mediaToolCatalog));
	let effectMap = $derived(new Map(effectList.map((entry) => [entry.id, entry])));
	let groups = $derived(effectGroups(effectList));
	let pending = $derived(edits.pending);
	let preview = $derived(previewSteps(edits));
	let effective = $derived(effectiveSteps(edits));
	let stackEffect = $derived(effective.findLast((entry) => effectMap.has(entry.tool))?.tool ?? null);
	let currentEffect = $derived(activeEffect && (effectMap.has(activeEffect) || !effectList.length) ? activeEffect : (stackEffect ?? groups[0]?.effects[0]?.id ?? null));
	let saveCompiled = $derived(compileSteps(effective, clip, { effects: effectMap }));
	let signature = $derived(ready ? stepsSignature(saveCompiled.steps) : savedSignature);
	let previewCompiled = $derived(compileSteps(preview, clip, { skipSplit: true, effects: effectMap }));
	let beforeCompiled = $derived(compileSteps(beforeSteps(edits), clip, { skipSplit: true, effects: effectMap }));
	let effectivePlayback = $derived(playbackPreview(effective, clip));
	let base = $derived(keptRange(duration, effectivePlayback.ops));

	let panelOp = $derived<VideoOp | null>(tool === 'effects' ? currentEffect : tool === 'replace' ? 'replace_audio' : tool === 'frame' ? null : tool);
	let panelParams = $derived.by<VideoParams>(() => {
		if (!panelOp) return {};
		if (pending && pending.tool === panelOp) return pending.params;
		return defaultsFor(panelOp);
	});
	let editingCrop = $derived(tool === 'crop' && ready && (pending?.tool === 'crop' || !effective.some((step) => step.tool === 'crop')));
	let playbackSteps = $derived(
		editingCrop
			? [...beforeSteps(edits), { ...draftFor('crop', { ...panelParams, rect: FULL_CROP }), id: 'crop-edit', hidden: false }]
			: preview
	);
	let playback = $derived(playbackPreview(playbackSteps, clip));
	let liveCompiled = $derived(compileSteps(playbackSteps, clip, { skipSplit: true, effects: effectMap }));
	let afterPlan = $derived<ChainPlan | null>(ready ? planFor(liveCompiled.steps) : null);
	let live = $derived(liveState({ webgl2, contextLosses, plan: afterPlan, steppedDown: previewCap < PREVIEW_CAPS[0], playing }));
	let beforePlan = $derived<ChainPlan | null>(live.mode === 'live' && compare ? planFor(beforeCompiled.steps) : null);
	let range = $derived(keptRange(duration, playback.ops));
	let scrubIn = $derived(tool === 'trim' && panelOp === 'trim' ? Number(panelParams.start ?? range.start) : range.start);
	let scrubOut = $derived(tool === 'trim' && panelOp === 'trim' ? Number(panelParams.end ?? range.end) : range.end);
	let outputLength = $derived(playbackPreview(preview, clip).duration);
	let displaySize = $derived(playback.size);
	let crop = $derived(playback.crop);
	let cropRect = $derived<CropRect>(editingCrop ? readCrop(panelParams).rect : FULL_CROP);
	let keepPitch = $derived(effective.concat(pending ? [{ ...pending, id: 'p', hidden: false }] : []).filter((step) => step.tool === 'speed').every((step) => readSpeed(step.params as VideoParams).keep_pitch));
	let splitting = $derived(hasSplit(saveCompiled.steps));
	let pendingMeaningful = $derived(!!pending && isVideoOp(pending.tool) && ready && (!!pending.target || !isNoop(pending.tool, pending.params, clip, base)));
	let hasEdits = $derived(saveCompiled.steps.length > 0 || pendingMeaningful);
	let visibleCount = $derived(effective.length);
	let dirty = $derived(ready ? dirtyCount(signature, savedSignature, visibleCount) : 0);
	let canSave = $derived(ready && !!inputRef && hasEdits);
	let canApply = $derived(!!panelOp && ready && (tool === 'mute' || !!pending?.target || !isNoop(panelOp, panelParams, clip, base)));
	let wantFrame = $derived(ready && !!inputRef && !playing && tool !== 'crop' && !clipPreview && live.serverPaused && hasPixelEffects(previewCompiled.steps));
	let frameKey = $derived(wantFrame ? JSON.stringify([previewCompiled.steps, Math.round(playhead * 1000)]) : null);
	let beforeKey = $derived(compare && ready && !!inputRef && !playing && !clipPreview && !live.canvas ? JSON.stringify([beforeCompiled.steps, Math.round(playhead * 1000)]) : null);
	let showAfterFrame = $derived(!!afterFrame && afterFrame.key === frameKey);
	let previewSignature = $derived(JSON.stringify(previewCompiled.steps));
	let sizeOptions = $derived(resizeOptions(compileSteps(beforeSteps(edits), clip).chain.size));
	let replaceOption = $derived(
		origin !== 'library'
			? { allowed: false, reason: 'Files from History stay as generated' }
			: splitting
				? { allowed: false, reason: 'A split saves two new items' }
				: { allowed: true }
	);
	let generationTabs = $derived($tabsStore.tabs.filter(isGenerationTab));
	let schemaKey = $derived(generationTabs.map((tab) => `${tab.id}:${tab.selectedPreset ?? ''}:${tab.selectedMode ?? ''}`).join('|'));
	let sendTargets = $derived.by(() => {
		const list = buildSendTargets(generationTabs, fieldsByTab);
		const first = list.findIndex((target) => target.id === preferredSend);
		return first > 0 ? [list[first], ...list.slice(0, first), ...list.slice(first + 1)] : list;
	});
	let renderLeft = $derived(render ? secondsLeft(render.percent / 100, Date.now() - render.startedAt) : null);
	let sourceInfo = $derived(origin === 'library' ? { icon: 'library', label: 'Library' } : origin === 'history' ? { icon: 'clock', label: 'History' } : null);

	function defaultsFor(op: VideoOp): VideoParams {
		if (op === 'trim') return { start: base.start, end: base.end };
		if (op === 'split') return { at: Math.round(Math.max(base.start, Math.min(base.end, playhead)) * 1000) / 1000 };
		return defaultParams(op, clip, 0, effectMap);
	}

	function planFor(steps: Parameters<typeof planChain>[0]['steps']): ChainPlan {
		return planChain({
			steps,
			source: { width: clip.width, height: clip.height, duration: clip.duration, fps },
			effects: effectMap,
			cap: previewCap,
			unavailable,
			analysis: (key) => measured.get(key) ?? null,
			stale: (toolId, index) => lastMeasured.get(`${toolId}:${index}`) ?? null
		});
	}

	function markUnavailable(toolId: string) {
		if (unavailable.has(toolId)) return;
		unavailable = new Set([...unavailable, toolId]);
	}

	function measure(need: AnalysisNeed) {
		if (!inputRef || analysisLoading.has(need.key)) return;
		analysisLoading.add(need.key);
		const input = inputRef;
		void effectAnalysis({ input, steps: need.steps, step_index: need.index })
			.then((values) => {
				if (destroyed) return;
				lastMeasured.set(`${need.tool}:${need.index}`, values);
				measured = new Map(measured).set(need.key, values);
			})
			.catch((error) => {
				logger.error('The clip could not be measured:', error);
				if (!destroyed) markUnavailable(need.tool);
			})
			.finally(() => analysisLoading.delete(need.key));
	}

	function foldPending(state: EditStackState<VideoParams>): EditStackState<VideoParams> {
		const current = state.pending;
		if (!current) return state;
		if (isVideoOp(current.tool) && ready && (current.target || !isNoop(current.tool, current.params, clip, base))) return apply(state);
		return discardPending(state);
	}

	function setParams(op: VideoOp, params: VideoParams) {
		const draft = draftFor(op, params, effectMap);
		edits = pending && pending.tool === op ? updatePending(edits, draft) : beginEdit(foldPending(edits), draft);
	}

	function selectTool(id: string) {
		if (!isRailToolId(id) || id === tool) return;
		edits = foldPending(edits);
		tool = id;
		compare = false;
		closeClipPreview();
	}

	function selectEffect(next: string) {
		if (next === currentEffect) return;
		edits = foldPending(edits);
		activeEffect = next;
	}

	function applyCurrent() {
		if (!panelOp || !canApply) return;
		const next = pending && pending.tool === panelOp ? edits : beginEdit(edits, draftFor(panelOp, panelParams, effectMap));
		edits = apply(next);
		compare = false;
	}

	function resetCurrent() {
		edits = discardPending(edits);
		aspect = 'free';
	}

	function setAspect(key: string) {
		aspect = key;
		const ratio = CROP_ASPECTS.find((entry) => entry.key === key)?.ratio ?? null;
		if (!ratio) return;
		const current = readCrop(panelParams);
		setParams('crop', { ...current, rect: fitAspect(current.rect, ratio, displaySize.width / Math.max(1, displaySize.height)) });
	}

	function reopenStep(id: string) {
		const step = edits.steps.find((entry) => entry.id === id);
		if (!step) return;
		const rail = railToolOf(step.tool);
		edits = reopen(edits, id);
		if (rail) tool = rail;
		if (rail === 'effects') activeEffect = step.tool;
	}

	function doUndo() {
		edits = undo(edits);
	}

	function doRedo() {
		edits = redo(edits);
	}

	function trimParams(): { start: number; end: number } {
		if (pending && pending.tool === 'trim') return { start: Number(pending.params.start), end: Number(pending.params.end) };
		return { start: base.start, end: base.end };
	}

	function setIn(seconds: number) {
		if (!ready) return;
		if (tool !== 'trim') selectTool('trim');
		const current = trimParams();
		const start = Math.max(0, Math.min(seconds, current.end - 0.05));
		setParams('trim', { start: Math.round(start * 1000) / 1000, end: current.end });
	}

	function setOut(seconds: number) {
		if (!ready) return;
		if (tool !== 'trim') selectTool('trim');
		const current = trimParams();
		const end = Math.min(duration, Math.max(seconds, current.start + 0.05));
		setParams('trim', { start: current.start, end: Math.round(end * 1000) / 1000 });
	}

	function seekTo(seconds: number) {
		const next = Math.max(0, Math.min(duration || 0, seconds));
		playhead = next;
		if (video && Number.isFinite(next)) video.currentTime = next;
		closeClipPreview();
	}

	function step(direction: -1 | 1) {
		video?.pause();
		seekTo(playhead + direction / fps);
		storedPlayhead = playhead;
	}

	function togglePlay() {
		if (!video || !ready) return;
		if (playing) {
			video.pause();
			return;
		}
		closeClipPreview();
		if (playhead < range.start - 0.01 || playhead >= range.end - 0.02) seekTo(range.start);
		void video.play().catch(() => undefined);
	}

	function frameLoop() {
		if (!video) return;
		const t = video.currentTime;
		if (t >= range.end - 0.005) {
			if (loop) video.currentTime = range.start;
			else {
				video.pause();
				video.currentTime = range.end;
			}
		}
		playhead = video.currentTime;
		if (playing) raf = requestAnimationFrame(frameLoop);
	}

	function onPlay() {
		playing = true;
		cancelAnimationFrame(raf);
		raf = requestAnimationFrame(frameLoop);
	}

	function onPause() {
		playing = false;
		cancelAnimationFrame(raf);
		if (video) playhead = video.currentTime;
		storedPlayhead = playhead;
	}

	function onMetadata() {
		if (!video) return;
		const reported = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Number(source?.metadata?.duration_seconds ?? 0);
		meta = { width: video.videoWidth || 1280, height: video.videoHeight || 720, duration: reported };
		videoError = false;
		const start = Math.min(storedPlayhead, Math.max(0, reported - 0.01));
		if (start > 0) seekTo(start);
	}

	function closeClipPreview() {
		if (!clipPreview) return;
		clipController?.abort();
		clipController = null;
		const runId = clipPreview.state === 'rendering' ? clipPreview.runId : null;
		if (runId) void cancelMediaToolRun(runId).catch(() => undefined);
		clipPreview = null;
	}

	async function previewClip() {
		if (!inputRef || !ready) return;
		video?.pause();
		const start = Math.max(range.start, Math.min(playhead, range.end - 0.05));
		const steps = previewCompiled.steps;
		const key = JSON.stringify([previewSignature, Math.round(start * 1000)]);
		const cached = clipCache.get(key);
		if (cached) {
			clipPreview = { key, sig: previewSignature, state: 'ready', url: cached, runId: null };
			return;
		}
		closeClipPreview();
		const controller = new AbortController();
		clipController = controller;
		clipPreview = { key, sig: previewSignature, state: 'rendering', url: null, runId: null };
		try {
			const started = await startClipPreview({ input: inputRef, steps, start_seconds: Math.round(start * 1000) / 1000, duration_seconds: 3, height: 480 });
			if (controller.signal.aborted) {
				if (started.run_id) void cancelMediaToolRun(started.run_id).catch(() => undefined);
				return;
			}
			let previewUrl = started.preview?.url ?? null;
			if (!previewUrl && started.run_id) {
				clipPreview = { key, sig: clipPreview?.sig ?? previewSignature, state: 'rendering', url: null, runId: started.run_id };
				const run = await trackRun(started.run_id, { api: runApi, signal: controller.signal, onProgress: () => undefined });
				if (!run || controller.signal.aborted) return;
				if (run.status !== 'completed') throw new Error(run.error?.message || 'The preview could not be rendered');
				previewUrl = run.preview?.url ?? null;
			}
			if (!previewUrl) throw new Error('The preview could not be rendered');
			clipCache.set(key, previewUrl);
			if (clipPreview?.key === key) clipPreview = { ...clipPreview, state: 'ready', url: previewUrl, runId: null };
		} catch (error) {
			if (controller.signal.aborted) return;
			clipPreview = null;
			toasts.show('error', error instanceof Error ? error.message : 'The preview could not be rendered', { title: 'Preview failed' });
		}
	}

	function stateSnapshot(): LoadedVideoState {
		return {
			stack: edits,
			tool,
			playhead: storedPlayhead,
			loop,
			savedSignature,
			saved,
			renderSignature: render?.signature ?? null,
			renderMode: render?.mode ?? null,
			renderStartedAt: render?.startedAt ?? null
		};
	}

	function announce(run: MediaToolRun, mode: string | null) {
		if (run.status === 'completed') {
			const items = run.items ?? [];
			if (mode === 'replace') {
				toasts.show('success', 'The Library item now holds your edits.', { title: 'Original replaced' });
				return;
			}
			toasts.show('success', items.length > 1 ? `${items.length} items are in your Library.` : 'Your edited clip is in your Library.', {
				title: 'Saved to Library',
				action: { label: 'Open', onClick: () => void goto('/library') }
			});
		} else if (run.status === 'failed') {
			toasts.show('error', run.error?.message || 'The render did not finish.', { title: 'Save failed' });
		} else {
			toasts.show('info', 'Your edits are kept.', { title: 'Render cancelled' });
		}
	}

	function fallbackSettle(run: MediaToolRun) {
		const tab = get(tabsStore).tabs.find((entry) => entry.id === tabId);
		if (!tab || !isToolTab(tab)) return;
		const loaded = readVideoState(tab.state);
		tabsStore.updateToolTab(tabId, { state: writeVideoState(settleState(loaded, run)), run: null });
		announce(run, loaded.renderMode);
	}

	const listener: RenderListener = {
		progress(run) {
			if (!render || render.runId !== run.run_id) return;
			const percent = percentOf(run);
			render = { ...render, percent };
			hostApi.setRun({ runId: run.run_id, progress: percent });
		},
		done(run) {
			const snap = render;
			render = null;
			optionsCollapsed = false;
			hostApi.setRun(null);
			const settled = settleState({ ...stateSnapshot(), renderSignature: snap?.signature ?? null, renderMode: snap?.mode ?? null }, run);
			savedSignature = settled.savedSignature;
			saved = settled.saved;
			const then = afterRender;
			afterRender = null;
			announce(run, snap?.mode ?? null);
			if (run.status === 'completed') then?.(run);
		}
	};

	function tabAlive(): boolean {
		return get(tabsStore).tabs.some((entry) => entry.id === tabId);
	}

	function follow(runId: string) {
		followRender(tabId, runId, runApi, listener, fallbackSettle, tabAlive);
	}

	async function startRender(mode: MediaToolSaveMode, then?: (run: MediaToolRun) => void) {
		if (!inputRef || !ready || render || starting) return;
		edits = foldPending(edits);
		const compiled = compileSteps(effectiveSteps(edits), clip, { effects: effectMap });
		if (compiled.steps.length === 0) return;
		const finalMode: MediaToolSaveMode = mode === 'update' && (!saved || hasSplit(compiled.steps)) ? 'new' : mode;
		const output: { mode: MediaToolSaveMode; target_item_id?: string } = { mode: finalMode };
		if (finalMode === 'update' && saved) output.target_item_id = saved.itemId;
		starting = true;
		try {
			const { run_id: runId } = await startMediaToolRun({ steps: compiled.steps, inputs: [inputRef], output });
			render = {
				runId,
				percent: 0,
				mode: finalMode,
				signature: stepsSignature(compiled.steps),
				startedAt: Date.now(),
				total: compiled.chain.duration,
				output: `MP4 · ${compiled.chain.size.width}×${compiled.chain.size.height}`,
				cancelling: false
			};
			afterRender = then ?? null;
			if (!phone) optionsCollapsed = true;
			hostApi.setRun({ runId, progress: 0 });
			follow(runId);
		} catch (error) {
			logger.error('The video render could not start:', error);
			toasts.show('error', error instanceof Error ? error.message : 'The render could not start', { title: 'Save failed' });
		} finally {
			starting = false;
		}
	}

	async function cancelRender() {
		if (!render || render.cancelling) return;
		const runId = render.runId;
		render = { ...render, cancelling: true };
		try {
			const snapshot = await cancelMediaToolRun(runId);
			if (snapshot && isTerminalStatus(snapshot.status)) settleRender(tabId, snapshot);
		} catch (error) {
			logger.error('The video render could not be cancelled:', error);
			if (render) render = { ...render, cancelling: false };
			toasts.show('warning', "Couldn't cancel. It may already be finished.");
		}
	}

	async function reattach(runId: string) {
		const total = saveCompiled.chain.duration;
		const restore = (percent: number) => {
			render = {
				runId,
				percent,
				mode: (initial.renderMode as MediaToolSaveMode) ?? 'new',
				signature: initial.renderSignature ?? signature,
				startedAt: initial.renderStartedAt ?? Date.now(),
				total,
				output: `MP4 · ${saveCompiled.chain.size.width}×${saveCompiled.chain.size.height}`,
				cancelling: false
			};
			if (!phone) optionsCollapsed = true;
		};
		if (isFollowing(tabId, runId)) {
			restore(Number(untrack(() => host.run?.progress) ?? 0));
			follow(runId);
			return;
		}
		try {
			const found = await findRun(runApi, runId);
			if (destroyed) return;
			if (!found) {
				hostApi.setRun(null);
				return;
			}
			restore(percentOf(found));
			if (isTerminalStatus(found.status)) listener.done(found);
			else follow(runId);
		} catch (error) {
			logger.error('Could not reattach the video render:', error);
			restore(Number(untrack(() => host.run?.progress) ?? 0));
			follow(runId);
		}
	}

	function sourceValue(): Record<string, unknown> {
		const ref = source!;
		return { path: ref.path, relative_path: ref.relative_path ?? ref.path, url: ref.url, name: ref.name ?? stem ?? 'Clip', type: 'video' };
	}

	function withOutput(use: (value: { url: string; field: Record<string, unknown> }) => void) {
		if (!source?.url) return;
		if (render || starting) {
			toasts.show('info', 'Wait for the current save to finish.', { title: 'Saving' });
			return;
		}
		if (signature === EMPTY_SIGNATURE && !pendingMeaningful) {
			use({ url: source.url, field: sourceValue() });
			return;
		}
		if (saved?.url && savedSignature === signature && !pendingMeaningful) {
			use({ url: saved.url, field: { ...sourceValue(), path: saved.path ?? saved.url, relative_path: saved.path ?? saved.url, url: saved.url, name: saved.name ?? stem } });
			return;
		}
		void startRender('new', (run) => {
			const item = run.items?.[0];
			if (item) use({ url: item.url, field: buildEditedMediaItem(item) as unknown as Record<string, unknown> });
		});
	}

	function sendTo(targetId: string) {
		const target = sendTargets.find((entry) => entry.id === targetId);
		if (!target) return;
		withOutput(({ field }) => deliver(target, field));
	}

	function deliver(target: VideoSendTarget, value: Record<string, unknown>) {
		const tab = get(tabsStore).tabs.find((entry) => entry.id === target.tabId);
		if (!tab || !isGenerationTab(tab)) return;
		tabsStore.updateTab(target.tabId, { formData: withFieldValue(tab.formData ?? {}, target, value) });
		tabsStore.setActiveTab(target.tabId);
		toasts.show('success', `${target.detail} in ${target.name} now holds this clip.`, { title: 'Sent to generation' });
	}

	function download(href: string) {
		const anchor = document.createElement('a');
		anchor.href = href;
		anchor.download = `${stem ?? 'clip'}-edit.mp4`;
		document.body.appendChild(anchor);
		anchor.click();
		anchor.remove();
	}

	function copyLink(href: string) {
		const absolute = new URL(href, window.location.origin).toString();
		navigator.clipboard
			?.writeText(absolute)
			.then(() => toasts.success('Link copied'))
			.catch(() => toasts.error("Couldn't copy the link"));
	}

	function onSave(action: SaveAction) {
		saveMenuOpen = false;
		saveSheetOpen = false;
		if (action.kind === 'save-new') void startRender('new');
		else if (action.kind === 'save') void startRender(saved ? 'update' : 'new');
		else if (action.kind === 'replace') void startRender('replace');
		else if (action.kind === 'send') sendTo(action.target);
		else if (action.kind === 'download') withOutput(({ url: href }) => download(href));
		else if (action.kind === 'copy') withOutput(({ url: href }) => copyLink(href));
	}

	function primarySave() {
		if (!canSave || render || starting) return;
		onSave(saved && !splitting ? { kind: 'save' } : { kind: 'save-new' });
	}

	async function frameAction(action: 'save' | 'open') {
		if (!inputRef || !ready || busy) return;
		busy = action === 'save' ? 'frame-save' : 'frame-open';
		video?.pause();
		try {
			const result = await previewFrame({ input: inputRef, steps: previewCompiled.steps, time_seconds: Math.round(playhead * 1000) / 1000, max_side: 4096, format: 'png' });
			const label = formatEditorTime(playhead).replace(':', 'm').replace('.', 's');
			const file = new File([result.blob], `${stem ?? 'clip'}-frame-${label}.png`, { type: 'image/png' });
			if (action === 'save') {
				const stored = await api.uploadMedia(file, 'user_upload');
				if (!stored.success) throw new Error(stored.message || 'The frame could not be saved');
				toasts.show('success', `${file.name} is in your Library.`, { title: 'Frame saved', action: { label: 'Open', onClick: () => void goto('/library') } });
			} else {
				const artifact = await uploadMediaToolArtifact(file);
				const ref = { path: artifact.url, url: artifact.url, name: file.name, type: 'image', item_id: artifact.id } as MediaRef;
				hostApi.openToolTab('image', ref, { activeTool: null });
			}
		} catch (error) {
			logger.error('Frame action failed:', error);
			toasts.show('error', error instanceof Error ? error.message : 'The frame could not be read', { title: 'Frame failed' });
		} finally {
			busy = null;
		}
	}

	function pickAudio(from: 'library' | 'upload') {
		if (from === 'library') pickers = 'audio';
		else audioInput?.click();
	}

	function useAudio(ref: MediaToolInputRef, name: string) {
		setParams('replace_audio', { ...panelParams, audio: ref, name });
	}

	async function uploadAudio(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		busy = 'audio';
		try {
			const artifact = await uploadMediaToolArtifact(file);
			useAudio({ item_id: artifact.id }, file.name);
		} catch (error) {
			toasts.show('error', error instanceof Error ? error.message : 'The file could not be uploaded', { title: 'Upload failed' });
		} finally {
			busy = null;
		}
	}

	function pickLibrary(upload: UploadFileInfo) {
		const pickedFor = pickers;
		pickers = null;
		if (pickedFor === 'audio') {
			useAudio({ item_id: upload.id }, upload.original_filename || upload.filename);
			return;
		}
		const path = `uploads/${upload.filename}`;
		void changeSource({ path, relative_path: path, url: upload.url, name: upload.original_filename || upload.filename, type: 'video', item_id: upload.id } as MediaRef);
	}

	function pickHistory(_generation: unknown, file: { id?: string | number; file_path: string } & Record<string, unknown>) {
		pickers = null;
		if (!file) return;
		const item = pickFromGeneration(file as never).item as Record<string, unknown>;
		const ref = { ...item, type: 'video' } as MediaRef & Record<string, unknown>;
		if (file.id !== undefined && file.id !== null) ref.generation_file_id = String(file.id);
		void changeSource(ref as MediaRef);
	}

	async function changeSource(next: MediaRef) {
		if (!source) {
			hostApi.setSource(next);
			return;
		}
		if (render || starting) {
			toasts.show('info', 'Wait for the current save to finish.', { title: 'Saving' });
			return;
		}
		await replaceSource(hostApi, source, next, dirty, 'clip');
	}

	function openReplacePicker(id: SourceEntryId) {
		if (id === 'library' || id === 'history') pickers = id;
	}

	function videoInnerStyle(): { box: string; media: string } {
		const rotation = crop?.rotation ?? 0;
		const rotated = rotation % 180 !== 0;
		const sourceAspect = clip.width / Math.max(1, clip.height);
		const shown = rotated ? 1 / sourceAspect : sourceAspect;
		const rect = crop && !editingCrop ? crop.rect : FULL_CROP;
		const box = `left: ${(-rect.x / rect.width) * 100}%; top: ${(-rect.y / rect.height) * 100}%; width: ${100 / rect.width}%; height: ${100 / rect.height}%;`;
		const width = rotated ? 100 / shown : 100;
		const height = rotated ? 100 * shown : 100;
		const sx = crop?.flipH ? -1 : 1;
		const sy = crop?.flipV ? -1 : 1;
		const media = `width: ${width}%; height: ${height}%; transform: translate(-50%, -50%) scale(${sx}, ${sy}) rotate(${rotation}deg);`;
		return { box, media };
	}

	let inner = $derived(videoInnerStyle());

	$effect(() => {
		const ref = source;
		inputRef = null;
		origin = null;
		refReason = null;
		if (!ref) return;
		const direct = directInputRef(ref);
		if (direct) {
			inputRef = direct;
			origin = 'item_id' in direct ? 'library' : 'history';
			return;
		}
		let cancelled = false;
		void resolveEditableResource(null, ref.relative_path || ref.path, 'video').then((resolved) => {
			if (cancelled) return;
			if (resolved.itemId) {
				inputRef = { item_id: resolved.itemId };
				origin = 'library';
			} else if (resolved.generationFileId) {
				inputRef = { generation_file_id: resolved.generationFileId };
				origin = 'history';
			} else refReason = resolved.reason;
		});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const href = url;
		const length = duration;
		const count = phone ? 8 : 12;
		if (!href || !(length > 0)) {
			frames = [];
			return;
		}
		frames = Array(count).fill(null);
		const controller = new AbortController();
		const grabber = createFrameGrabber(href, phone ? 40 : 48);
		const times = stripTimes(length, count);
		void runStrip({
			count,
			signal: controller.signal,
			work: (index, signal) => grabber.grab(times[index], signal),
			onResult: (index, value) => {
				frames = frames.map((frame, i) => (i === index ? value : frame));
				if (index === 0) hostApi.setThumbnail(value);
			},
			pause: () => abortableWait(0, controller.signal)
		}).finally(() => grabber.dispose());
		return () => controller.abort();
	});

	$effect(() => {
		const href = url;
		const length = duration;
		peaks = null;
		if (!href || !(length > 0) || phone) return;
		const controller = new AbortController();
		void loadPeaks(href, 160, controller.signal).then((result) => {
			if (!controller.signal.aborted) peaks = result;
		});
		return () => controller.abort();
	});

	function fetchFrame(key: string | null, steps: () => unknown[], assign: (value: { key: string; url: string } | null) => void, current: () => { key: string; url: string } | null) {
		if (!key || !inputRef) return;
		const input = inputRef;
		const time = Math.round(playhead * 1000) / 1000;
		const list = untrack(steps);
		return requestFrame({
			key,
			load: async (signal) => (await previewFrame({ input, steps: list as never, time_seconds: time, max_side: 1280, format: 'webp' }, signal)).blob,
			current,
			assign: (slot) => {
				if (destroyed) URL.revokeObjectURL(slot.url);
				else assign(slot);
			},
			onError: (error) => logger.error('Frame preview failed:', error)
		});
	}

	$effect(() => {
		const key = frameKey;
		return fetchFrame(key, () => previewCompiled.steps, (value) => (afterFrame = value), () => untrack(() => afterFrame));
	});

	$effect(() => {
		const key = beforeKey;
		return fetchFrame(key, () => beforeCompiled.steps, (value) => (beforeFrame = value), () => untrack(() => beforeFrame));
	});

	$effect(() => {
		const sig = previewSignature;
		if (untrack(() => !!clipPreview && clipPreview.sig !== sig)) untrack(closeClipPreview);
	});

	$effect(() => {
		const el = video;
		if (!el) return;
		el.playbackRate = Math.max(0.0625, Math.min(16, playback.rate));
		(el as HTMLVideoElement & { preservesPitch?: boolean }).preservesPitch = keepPitch;
		el.volume = Math.max(0, Math.min(1, playback.gain));
		el.muted = playback.muted || playbackMuted;
	});

	$effect(() => {
		const key = schemaKey;
		const tabs = untrack(() => generationTabs);
		void key;
		for (const tab of tabs) {
			const presetId = tab.selectedPreset;
			const mode = tab.selectedMode;
			if (!presetId || !mode) continue;
			getCachedSchema(presetId, mode, async () => {
				const response = await api.getPresetFormSchema(presetId, mode);
				if (!response.success || !response.data?.form_schema) throw new Error('No form schema');
				return response.data.form_schema;
			})
				.then((schema) => {
					if (!destroyed) fieldsByTab = { ...fieldsByTab, [tab.id]: videoFieldsOf(schema) };
				})
				.catch(() => undefined);
		}
	});

	$effect(() => {
		hostApi.setState(writeVideoState(stateSnapshot()));
	});

	$effect(() => {
		hostApi.setDirty(dirty);
	});

	$effect(() => {
		if (stem) hostApi.setTitle(stem);
	});

	$effect(() => {
		hostApi.setActiveTool(tool);
	});

	$effect(() => {
		const requested = host.activeTool;
		if (isRailToolId(requested) && requested !== untrack(() => tool)) untrack(() => selectTool(requested));
	});

	$effect(() => {
		const hint = (host as HintedHost).hint;
		if (!hint) return;
		untrack(() => {
			if (hint.effect && isEffectOp(hint.effect)) {
				if (tool !== 'effects') selectTool('effects');
				selectEffect(hint.effect);
			}
			if (hint.sendTo) preferredSend = `${hint.sendTo.tabId}::${hint.sendTo.field}`;
			(hostApi as HintedHost).clearHint?.();
		});
	});

	$effect(() => {
		if (!phone) {
			hostApi.setActions(null);
			return;
		}
		hostApi.setActions({
			undo: { label: undoLabel(edits) ? `Undo ${undoLabel(edits)}` : 'Undo', disabled: !canUndo(edits), run: doUndo },
			redo: null,
			save: { label: 'Save', disabled: !canSave, progress: render ? render.percent : null, run: () => (saveSheetOpen = true) }
		});
	});

	$effect(() => {
		const plans = [afterPlan, beforePlan];
		for (const plan of plans) {
			for (const pass of plan?.passes ?? []) {
				if (pass.kind !== 'effect' || shaderTexts.has(pass.shaderUrl) || shaderLoading.has(pass.shaderUrl)) continue;
				const url = pass.shaderUrl;
				const toolId = pass.tool;
				shaderLoading.add(url);
				void effectShader(url)
					.then((text) => {
						if (!destroyed) shaderTexts = new Map(untrack(() => shaderTexts)).set(url, text);
					})
					.catch((error) => {
						logger.error('The effect preview could not be loaded:', error);
						if (!destroyed) untrack(() => markUnavailable(toolId));
					})
					.finally(() => shaderLoading.delete(url));
			}
		}
	});

	$effect(() => {
		for (const plan of [afterPlan, beforePlan]) {
			for (const pass of plan?.passes ?? []) {
				if (pass.kind !== 'effect') continue;
				const toolId = pass.tool;
				for (const texture of pass.textures) {
					const ref = texture.ref;
					if (!ref || lutTables.has(ref) || lutLoading.has(ref)) continue;
					lutLoading.add(ref);
					void textureTable(texture)
						.then((table) => {
							if (!destroyed) lutTables = new Map(untrack(() => lutTables)).set(ref, table);
						})
						.catch((error) => {
							logger.error('The LUT preview could not be loaded:', error);
							if (!destroyed) untrack(() => markUnavailable(toolId));
						})
						.finally(() => lutLoading.delete(ref));
				}
			}
		}
	});

	$effect(() => {
		const needs = [...(afterPlan?.analysis ?? []), ...(beforePlan?.analysis ?? [])];
		if (!needs.length || !inputRef) return;
		const timer = setTimeout(() => untrack(() => needs.forEach(measure)), 150);
		return () => clearTimeout(timer);
	});

	onMount(() => {
		webgl2 = supportsWebGL2();
		void loadMediaToolCatalog();
		const run = untrack(() => host.run);
		if (run?.runId) void reattach(run.runId);
	});

	onDestroy(() => {
		destroyed = true;
		cancelAnimationFrame(raf);
		detachRender(tabId, listener);
		if (!tabAlive()) stopRender(tabId);
		clipController?.abort();
		if (afterFrame) URL.revokeObjectURL(afterFrame.url);
		if (beforeFrame) URL.revokeObjectURL(beforeFrame.url);
		hostApi.setActions(null);
	});
</script>

{#snippet emptyState()}
	<div class="dot-grid flex h-full min-h-0 flex-1 items-center justify-center p-6">
		<div class="flex w-full max-w-md flex-col items-center rounded-lg border border-dashed border-line-strong bg-surface-1 px-8 py-10 text-center" data-testid="tool-tab-empty">
			<span class="mb-4 flex h-11 w-11 items-center justify-center rounded border border-line bg-surface-2 text-fg-muted">
				<Icon name="video" className="h-5 w-5" />
			</span>
			<h2 class="text-base font-semibold text-fg">Open a clip to edit</h2>
			<p class="mt-2 text-sm text-fg-muted">Pick a video from your Library or History. Edit from History or Library opens it here too.</p>
			<div class="mt-5 flex gap-2">
				{#each sourceEntries('video') as entry (entry.id)}
					<Button variant="secondary" size="sm" icon={entry.icon} onclick={() => openReplacePicker(entry.id)}>{entry.label}</Button>
				{/each}
			</div>
		</div>
	</div>
{/snippet}

{#snippet replaceMenu()}
	<ReplaceMenu label="Replace clip" entries={sourceEntries('video')} disabled={!!render || starting} {phone} onselect={openReplacePicker} />
{/snippet}

{#snippet saveRows()}
	<div role="menu" aria-label="Save" class="flex flex-col">
		<SaveMenuRows
			saved={!!saved && !splitting}
			replace={replaceOption}
			sendTargets={sendTargets}
			downloadFormat="MP4"
			copyLabel="Copy link"
			onchoose={onSave}
		/>
	</div>
{/snippet}

<input bind:this={audioInput} type="file" accept="audio/*,video/*" class="hidden" onchange={uploadAudio} data-replace-input />

<MediaFieldPickers
	historyOpen={pickers === 'history'}
	libraryOpen={pickers === 'library' || pickers === 'audio'}
	kind={pickers === 'audio' ? 'audio' : 'video'}
	onHistoryClose={() => (pickers = null)}
	onLibraryClose={() => (pickers = null)}
	onHistorySelect={pickHistory}
	onLibrarySelect={pickLibrary}
/>

{#if !source}
	{@render emptyState()}
{:else}
	<div class="flex h-full min-h-0 flex-col" data-testid="video-editor" data-ready={ready || undefined} data-phone={phone || undefined}>
		<EditorFrame
			{phone}
			bind:compare
			bind:stackOpen
			canCompare={ready}
			editCount={visibleCount}
			phoneChips="bottom"
			onundo={doUndo}
			onredo={doRedo}
			onsave={primarySave}
			onapply={applyCurrent}
		>
			{#snippet phoneLead()}
				{@render replaceMenu()}
			{/snippet}

			{#snippet toolbar()}
				{#if !phone}
					<EditorToolbar
						fileName={source?.name ?? stem}
						emptyLabel="No clip"
						chips={ready ? [`${clip.width}×${clip.height}`, `${fixed2(outputLength)} s`] : []}
						source={sourceInfo}
						canUndo={canUndo(edits)}
						canRedo={canRedo(edits)}
						undoLabel={undoLabel(edits)}
						redoLabel={redoLabel(edits)}
						{compare}
						canCompare={ready}
						{canSave}
						saved={!!saved && !splitting}
						replace={replaceOption}
						{sendTargets}
						downloadFormat="MP4"
						copyLabel="Copy link"
						saving={render ? { percent: render.percent, label: 'Saving' } : null}
						bind:saveMenuOpen
						onundo={doUndo}
						onredo={doRedo}
						oncompare={(next) => (compare = next)}
						onsave={onSave}
						oncancelsave={cancelRender}
					>
						{#snippet replaceControl()}
							{@render replaceMenu()}
						{/snippet}
					</EditorToolbar>
				{/if}
			{/snippet}

			{#snippet rail()}
				<ToolRail
					groups={RAIL_GROUPS}
					active={tool}
					pending={pending ? [railToolOf(pending.tool) ?? ''] : []}
					variant={phone ? 'strip' : 'rail'}
					label="Video tools"
					onselect={selectTool}
				/>
			{/snippet}

			{#snippet canvas()}
				<EditorCanvas
					mediaWidth={displaySize.width}
					mediaHeight={displaySize.height}
					bind:viewport
					bind:split
					{compare}
					{phone}
					before={live.canvas ? undefined : beforeLayer}
					afterLabel="After · {formatEditorTime(playhead)}"
					hint={tool === 'trim' ? trimHint : wantFrame ? effectsHint : undefined}
				>
					{#snippet layer()}
						<div class="absolute inset-0 overflow-hidden bg-scrim" data-video-stage>
							<div class="absolute" style={inner.box}>
								<video
									bind:this={video}
									src={url}
									class="absolute left-1/2 top-1/2 max-w-none object-fill"
									style={inner.media}
									preload="auto"
									playsinline
									onloadedmetadata={onMetadata}
									onplay={onPlay}
									onpause={onPause}
									onerror={() => (videoError = true)}
									data-video-element
								><track kind="captions" /></video>
							</div>
							{#if live.mode === 'live' && afterPlan}
								<LivePreview
									{video}
									after={afterPlan}
									before={beforePlan}
									{compare}
									{split}
									{fps}
									duration={clip.duration}
									sources={shaderTexts}
									tables={lutTables}
									baseGain={playback.gain}
									visible={live.canvas}
									onunsupported={() => (webgl2 = false)}
									onfailed={markUnavailable}
									onlost={() => (contextLosses += 1)}
									oncap={(cap) => (previewCap = cap)}
									onslow={markUnavailable}
								/>
							{/if}
							{#if showAfterFrame && afterFrame}
								<img src={afterFrame.url} alt="" class="absolute inset-0 h-full w-full object-fill" data-video-frame-preview />
							{/if}
							{#if clipPreview?.state === 'ready' && clipPreview.url}
								<video src={clipPreview.url} class="absolute inset-0 h-full w-full bg-scrim object-contain" autoplay loop muted={playback.muted || playbackMuted} playsinline data-video-clip-preview><track kind="captions" /></video>
							{/if}
						</div>
					{/snippet}
					{#snippet overlay()}
						{#if editingCrop}
							<CropOverlay
								rect={cropRect}
								ratio={CROP_ASPECTS.find((entry) => entry.key === aspect)?.ratio ?? null}
								stageAspect={displaySize.width / Math.max(1, displaySize.height)}
								onchange={(rect) => setParams('crop', { ...readCrop(panelParams), rect })}
							/>
						{/if}
					{/snippet}
					{#snippet badge()}
						{#if clipPreview?.state === 'ready'}
							<span class="pointer-events-auto inline-flex items-center gap-1.5">
								<CanvasChip>Preview 3 s · 480p</CanvasChip>
								<button type="button" class="grid h-6 w-6 place-items-center rounded bg-scrim/60 text-scrim-on" aria-label="Close preview" onclick={closeClipPreview} data-close-clip-preview>
									<Icon name="close" className="h-3.5 w-3.5" />
								</button>
							</span>
						{:else if clipPreview?.state === 'rendering'}
							<CanvasChip>Rendering preview…</CanvasChip>
						{:else if videoError}
							<CanvasChip>This clip could not be played</CanvasChip>
						{:else if !ready}
							<CanvasChip>Loading clip…</CanvasChip>
						{:else if !playing || live.chips.length}
							<span class="inline-flex flex-wrap items-center gap-1.5" data-live-chips>
								{#if !playing}
									<CanvasChip>Paused · {formatEditorTime(playhead)}</CanvasChip>
								{/if}
								{#each live.chips as chip (chip)}
									<CanvasChip>{chip}</CanvasChip>
								{/each}
							</span>
						{/if}
					{/snippet}
				</EditorCanvas>
			{/snippet}

			{#snippet below()}
				<ScrubBar
					duration={duration}
					{playhead}
					inPoint={scrubIn}
					outPoint={scrubOut}
					outputLength={outputLength}
					handles={tool === 'trim' && ready}
					{playing}
					{loop}
					muted={playbackMuted}
					frameStep={1 / fps}
					{frames}
					{peaks}
					compact={phone}
					active={ready && !pickers && !saveSheetOpen}
					onseek={(seconds) => {
						video?.pause();
						seekTo(seconds);
						storedPlayhead = seconds;
					}}
					onin={setIn}
					onout={setOut}
					ontoggleplay={togglePlay}
					onstep={step}
					ontoggleloop={() => (loop = !loop)}
					ontogglemute={() => (playbackMuted = !playbackMuted)}
				/>
			{/snippet}

			{#snippet panelTop()}
				{#if render && !phone}
					<RenderCard
						percent={render.percent}
						total={render.total}
						output={render.output}
						left={renderLeft}
						label={render.mode === 'replace' ? 'Replacing original' : render.mode === 'update' ? 'Saving' : 'Saving as new'}
					/>
				{/if}
			{/snippet}

			{#snippet options()}
				{#if phone && render}
					<RenderCard
						phone
						percent={render.percent}
						total={render.total}
						output={render.output}
						left={renderLeft}
						cancelling={render.cancelling}
						label={render.mode === 'replace' ? 'Replacing original' : 'Saving as new'}
						oncancel={cancelRender}
					/>
				{:else}
					<OptionsPanel
						title={toolLabels[tool]}
						icon={toolIcons[tool]}
						bind:collapsed={optionsCollapsed}
						applyLabel={tool === 'trim' ? 'Apply trim' : tool === 'effects' ? 'Apply effect' : tool === 'mute' ? 'Mute' : 'Apply'}
						{canApply}
						canReset={!!pending}
						{phone}
						onapply={applyCurrent}
						onreset={resetCurrent}
					>
						{#if ready}
							<VideoToolOptions
								{tool}
								op={panelOp}
								params={panelParams}
								effect={currentEffect}
								effects={groups}
								showClip={live.showClip}
								{clip}
								{playhead}
								{outputLength}
								{aspect}
								{sizeOptions}
								{phone}
								previewState={clipPreview?.state ?? 'idle'}
								{busy}
								onparams={setParams}
								onaspect={setAspect}
								oneffect={selectEffect}
								onsetin={() => setIn(playhead)}
								onsetout={() => setOut(playhead)}
								onpreview={previewClip}
								oncancelpreview={closeClipPreview}
								onframe={frameAction}
								onpickaudio={pickAudio}
							/>
						{:else if refReason}
							<p class="text-sm text-warning">{refReason}</p>
						{/if}
					</OptionsPanel>
				{/if}
			{/snippet}

			{#snippet stack(variant)}
				<div class="{variant === 'panel' ? 'flex max-h-[45%] min-h-0 flex-none flex-col border-t border-line' : ''}">
					<EditStack
						stack={edits}
						{variant}
						original={{ summary: ready ? `${fixed2(duration)} s · ${clip.width}×${clip.height}` : 'Loading…', thumb: frames[0] ?? null }}
						onreopen={reopenStep}
						onseek={(cursor) => (edits = seek(edits, cursor))}
						ontogglehidden={(id) => (edits = toggleHidden(edits, id))}
						onremove={(id) => (edits = remove(edits, id))}
						onmove={(id, to) => (edits = move(edits, id, to))}
						onundo={doUndo}
						onredo={doRedo}
					>
						{#snippet notes()}
							{#if render}
								<div class="mx-2 mb-2 flex gap-2 rounded bg-surface-2 p-2.5 text-sm text-fg-muted" data-render-note>
									<Icon name="info" className="mt-0.5 h-3.5 w-3.5 flex-none" />
									<span>Saving renders a snapshot of these edits. You can keep editing; new edits stay unsaved.</span>
								</div>
							{/if}
						{/snippet}
					</EditStack>
				</div>
			{/snippet}
		</EditorFrame>

		{#if phone && saveSheetOpen}
			<FrameSheet label="Save" onclose={() => (saveSheetOpen = false)}>
				{#snippet header()}
					<span class="text-lg font-semibold text-fg">Save</span>
				{/snippet}
				{@render saveRows()}
			</FrameSheet>
		{/if}
	</div>
{/if}

{#snippet beforeLayer()}
	<div class="absolute inset-0 bg-scrim">
		{#if beforeFrame && beforeFrame.key === beforeKey}
			<img src={beforeFrame.url} alt="" class="h-full w-full object-fill" data-video-before />
		{/if}
	</div>
{/snippet}

{#snippet trimHint()}
	<CanvasChip caps={false}>K</CanvasChip>play / pause
{/snippet}

{#snippet effectsHint()}
	Effects preview on this frame
{/snippet}
