<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { get } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import { pickFromUpload } from '$lib/components/form-fields/mediaLoaderPicks';
	import { canvasToPngFile } from '$lib/paint/flatten';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
	import { Button, IconButton, Kbd, Spinner } from '$lib/components/ui';
	import {
		CanvasChip,
		EditStack,
		EditorCanvas,
		EditorFrame,
		EditorToolbar,
		FrameSheet,
		LookList,
		OptionsPanel,
		PropertySlider,
		ToolRail
	} from '$lib/components/tool-tabs/frame';
	import SaveMenuRows from '$lib/components/tool-tabs/frame/SaveMenuRows.svelte';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';
	import { editorsRevision, entryKey } from '$lib/editors/registry';
	import { railTools } from '$lib/editors/railTools';
	import { reopenToolOf, stepKinds } from '$lib/editors/stepKinds';
	import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
	import { imageRowInfo, outOfDateCount, outOfDateNotice, runStepAction, stepOutOfDate, stepsBeneath } from '$lib/tool-tabs/image/stepKinds';
	import { createTabGenerate } from '$lib/tool-tabs/image/generate/wiring';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { isLargeArea } from '$lib/tool-tabs/image/generate/area';
	import { generateSteps, leftOutCount, leftOutNotice, shapeChange, stepsBeneathOf } from '$lib/tool-tabs/image/generate/generateStep';
	import { runViews } from '$lib/tool-tabs/image/generate/runEngine';
	import { settingsOf } from '$lib/tool-tabs/image/generate/register';
	import { canvasFromUrl } from '$lib/paint/loadPicked';
	import FloatingPrompt from '$lib/components/tool-tabs/generate/FloatingPrompt.svelte';
	import CandidateStrip from '$lib/components/tool-tabs/generate/CandidateStrip.svelte';
	import RunChip from '$lib/components/tool-tabs/generate/RunChip.svelte';
	import RunOverlay from '$lib/components/tool-tabs/generate/RunOverlay.svelte';
	import StaleOutline from '$lib/components/tool-tabs/generate/StaleOutline.svelte';
	import { LASSO_OVERLAY, chooseOverlay, maskArea, maskToolState } from '$lib/tool-tabs/image/maskOverlays';
	import { createImageToolHost } from '$lib/tool-tabs/image/toolHost';
	import { paintFilters } from '$lib/paint/registries';
	import { filterCatalog, loadFilterCatalog } from '$lib/filters/catalog';
	import { getOp } from '$lib/filters/engine';
	import { sharedExtensions } from '$lib/filters/extensions';
	import { buildStrip, isLocked } from '$lib/filters/strip';
	import { LookThumbPainter, drawSource } from '$lib/filters/thumbPainter';
	import type { FilterItem } from '$lib/filters/types';
	import { loadPresetFormSchema } from '$lib/form/formSources';
	import { loadImage } from '$lib/media/loadImage';
	import { resolveEditableResource } from '$lib/media/editorSource';
	import { EMPTY_IMAGE_PLAN, planOutputSize } from '$lib/media/imageEditPlan';
	import { displaySize } from '$lib/media/cropGeometry';
	import { loadMediaToolCatalog, mediaToolCatalog, mediaToolCatalogRevision } from '$lib/media-tools/catalog';
	import { describeMediaToolFailure } from '$lib/media-tools/errors';
	import { announceOutcome } from '$lib/media-tools/execute';
	import { initialValues } from '$lib/media-tools/params';
	import { mediaToolRuns } from '$lib/media-tools/runs';
	import type { MediaToolInputRef, MediaToolItem } from '$lib/media-tools/types';
	import { blockedUploadMessage } from '$lib/plans/uploadLimit';
	import { api } from '$lib/services/api/index';
	import type { UploadFileInfo } from '$lib/services/api/media';
	import { uploadMediaToolArtifact } from '$lib/services/api/mediaTools';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import { tabsStore } from '$lib/stores/tabs';
	import { confirmRemoveAll } from '$lib/tool-tabs/frame/removeAll';
	import { toasts } from '$lib/stores/toast';
	import { landOnHistoryResult } from '$lib/tools/historyResult';
	import {
		apply,
		appliedCount,
		appliedSteps,
		beforeSteps,
		beginEdit,
		reopenLatest,
		canRedo,
		canUndo,
		commitLive,
		createStack,
		discardPending,
		effectiveSteps,
		isLive,
		move,
		previewWith,
		redo,
		redoLabel,
		remove,
		reopen,
		seek,
		toggleHidden,
		undo,
		undoLabel,
		updatePending,
		type EditStackState,
		type EditStep,
		type StepDraft
	} from '$lib/tool-tabs/frame/editStack';
	import type { RailGroup, SaveAction, SavingProgress } from '$lib/tool-tabs/frame/types';
	import { fitViewport } from '$lib/tool-tabs/frame/viewport';
	import { geometryKey, liveMask, maskChannelOf, maskIsStale, sendPatch, type MaskChannel } from '$lib/tool-tabs/image/mask';
	import { inputRef, outputSize, replaceOption, runBody, sourceOrigin, type PixelSize, type SourceOrigin } from '$lib/tool-tabs/image/payload';
	import { FULL_SIDE, ImageRenderer, bakeIsCapped, isAbort, orient, stepsKey, type Rendered } from '$lib/tool-tabs/image/render';
	import { showCanvas } from '$lib/tool-tabs/image/displayCanvas';
	import { areaIsMarked, landThenSend } from '$lib/tool-tabs/image/sendLanding';
	import { imageFieldsOf, sendTargetsFor, type ImageField } from '$lib/tool-tabs/image/sendTargets';
	import { artifactRef, pastedImage } from '$lib/tool-tabs/image/sourceRefs';
	import { readImageState, stackSignature, unsavedEdits, writeImageState, type SavedCopy } from '$lib/tool-tabs/image/state';
	import { createEditAutosave, type AutosaveStatus } from '$lib/tool-tabs/image/editAutosave';
	import { editAutosaveOutbox } from '$lib/tool-tabs/image/editAutosaveOutbox';
	import { editSourceOf, readEditLink, withEditLink } from '$lib/tool-tabs/image/editLink';
	import { editLinkForSave } from '$lib/tool-tabs/image/saveLink';
	import EditConflictNotice from '$lib/components/tool-tabs/frame/EditConflictNotice.svelte';
	import EditSaveIndicator from '$lib/components/tool-tabs/frame/EditSaveIndicator.svelte';
	import EditorMenuSheet from '$lib/components/tool-tabs/frame/EditorMenuSheet.svelte';
	import { ensureCoreEditorMenu } from '$lib/editors/coreMenu';
	import type { EditorMenuCommands, EditorMenuContext } from '$lib/editors/menuItems';
	import { createMenuRecents, openAllEdits, openRecentEdit } from '$lib/tool-tabs/image/menuEdits';
	import { statusParts } from '$lib/tool-tabs/frame/statusLine';
	import { requestCloseTab } from '$lib/tabs/closeConfirm';
	import {
		CATALOG_TOOL,
		LEGACY_TOOL_ALIAS,
		IMAGE_PINNED_LANES,
		adjustDraft,
		catalogDraft,
		cropDraft,
		imageRail,
		isRailTool,
		isCoreTool,
		lookDraft,
		paintDraft,
		resizeDraft,
		sliceDraft,
		toolEffectDraft,
		toolMeta,
		type CatalogParams,
		type CropParams,
		type EffectParams,
		type PaintParams,
		type ResizeParams,
		type ToolEffectParams
	} from '$lib/tool-tabs/image/steps';
	import { mediaFileStem } from '$lib/tool-tabs/sources';
	import { imageEffects } from '$lib/tool-tabs/image/shaderEffect';
	import type { ToolTabHost } from '$lib/tool-tabs/types';
	import { isGenerationTab, userGenerationTabs, type GenerationTab, type MediaRef } from '$lib/types/tabs';
	import { analyseImage, detectPanels, type Analysis, type Rect } from '$lib/tool-tabs/image/slice/detect';
	import { cropCanvas as sliceCrop, zipStore } from '$lib/tool-tabs/image/slice/crops';
	import {
		detectCells,
		emptyHistory,
		emptySlice,
		gridCells,
		hexColor,
		kept,
		maxOverlap,
		mergeCells,
		numbering,
		outputNames,
		outputSize as cellOutputSize,
		record,
		redoSlice,
		renameCell,
		reorderKept,
		splitCell,
		toggleSkip,
		deleteCell,
		undoSlice,
		type Cell,
		type SliceHistory,
		type SliceParams
	} from '$lib/tool-tabs/image/slice/model';
	import { referenceFieldsOf, referencePatch, referenceTargetsFor, type ReferenceField, type ReferenceTarget } from '$lib/tool-tabs/image/slice/references';
	import { handOffToNewTab } from '$lib/tool-tabs/image/slice/handoff';
	import { activeSlice, canMoveWithSlice, isSlice, sliceLast, withoutSlice } from '$lib/tool-tabs/image/slice/stack';
	import SliceOptions, { type NameRow } from './slice/SliceOptions.svelte';
	import SliceOverlay from './slice/SliceOverlay.svelte';
	import SliceSaveRows from './slice/SliceSaveRows.svelte';
	import SliceStrip, { type StripItem } from './slice/SliceStrip.svelte';
	import { appliedEntries, entryOfStep, isLiveEffectEntry, isLiveEntry, OP_TO_ENTRY, resetEntry } from '$lib/tool-tabs/image/entries';
	import { effectDefaults } from '$lib/effects/catalog';
	import { imageEffects as imageEffectList } from '$lib/tool-tabs/image/shaderEffect';
	import { canApplyEffect, canResetEffect, effectDefaultsOf } from '$lib/tool-tabs/image/effectState';
	import CatalogToolOptions from './CatalogToolOptions.svelte';
	import CropOptions, { aspectLabel, IMAGE_ASPECTS } from './CropOptions.svelte';
	import CropOverlay from './CropOverlay.svelte';
	import ImageStartScreen from './ImageStartScreen.svelte';
	import ImageSourcePicker from './ImageSourcePicker.svelte';
	import RecentPictures from './RecentPictures.svelte';
	import ReplaceMenu from '../frame/ReplaceMenu.svelte';
	import { replaceSource, sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import { currentSourceOf } from '$lib/components/form-fields/mediaFieldSources';
	import { isEditableTarget } from '$lib/utils/editableTarget';
	import { pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import MaskLayer from './MaskLayer.svelte';
	import MaskOptions from './MaskOptions.svelte';
	import PaintOptions from './PaintOptions.svelte';
	import { canonicalTool, isPaintEntry, paintEntryId, paintToolOfEntry } from '$lib/paint/entries';
	import { paintToolForKey } from '$lib/paint/toolKeys';
	import PaintStage from './PaintStage.svelte';
	import ResizeOptions from './ResizeOptions.svelte';

	type ImageHint = { look?: string; effect?: string; orientation?: 'rotate' | 'flip'; sendTo?: { tabId: string; field: string }; detect?: boolean; suggestSlice?: boolean };
	type HintedHost = ToolTabHost & { readonly hint?: ImageHint | null; clearHint?: () => void };

	let { host }: { host: ToolTabHost } = $props();

	function knownTool(id: string | null | undefined): string | null {
		if (!id) return null;
		ensureCoreImageEditor();
		const wanted = LEGACY_TOOL_ALIAS[id] ?? id;
		return isCoreTool(wanted) || wanted.includes('.') || railTools.get('image', wanted) ? wanted : 'crop';
	}

	const initial = untrack(() => readImageState(host.state, knownTool(host.activeTool)));
	let stack = $state.raw<EditStackState<unknown>>(sliceLast(initial.stack));
	let activeTool = $state<string | null>(knownTool(initial.activeTool));
	let sendFirst = $state.raw<{ tabId: string; field: string } | null>(null);
	let viewport = $state(initial.viewport);
	let mask = $state.raw<MaskChannel | null>(initial.mask);
	let saved = $state.raw<SavedCopy | null>(initial.saved);
	let compare = $state(false);
	let stackOpen = $state(false);
	let editsHidden = $state(false);
	let phoneMenuOpen = $state(false);
	let canvasApi = $state<ReturnType<typeof EditorCanvas> | undefined>();
	const menuRecents = createMenuRecents();
	let saveMenuOpen = $state(false);
	let saving = $state<SavingProgress | null>(null);
	let busy = $state(false);
	let notice = $state<string | null>(null);
	let query = $state('');
	let hoverLook = $state<string | null>(null);
	let thumbSource = $state.raw<ImageData | null>(null);
	let liveTimer: ReturnType<typeof setTimeout> | null = null;
	const LIVE_COMMIT_MS = 600;
	const thumbPainter = new LookThumbPainter(() => extensions);

	let phone = $derived(host.phone);
	let source = $derived(host.source);
	let sourceVersion = $state(0);
	let sourceUrl = $derived(source?.url ? (sourceVersion ? `${source.url}${source.url.includes('?') ? '&' : '?'}v=${sourceVersion}` : source.url) : null);
	let sourceSize = $state.raw<PixelSize | null>(null);
	let sourceError = $state<string | null>(null);
	let resolvedRef = $state.raw<MediaToolInputRef | null>(null);
	let origin = $state<SourceOrigin>('unknown');
	let emptyBusy = $state(false);
	let emptyError = $state<string | null>(null);

	const renderer = new ImageRenderer({ url: '', size: { width: 1, height: 1 }, ref: null });
	const renders = new Map<string, Promise<Rendered>>();

	let mainRender = $state.raw<Rendered | null>(null);
	let beforeRender = $state.raw<Rendered | null>(null);
	let thumbRender = $state.raw<Rendered | null>(null);
	let paintBase = $state.raw<Rendered | null>(null);
	let renderError = $state<string | null>(null);
	let rendering = $state(false);
	let surfaceVersion = $state(0);

	let paintStage = $state<ReturnType<typeof PaintStage> | undefined>();
	let paintSession = $state.raw<PaintSession | null>(null);
	let paintState = $state.raw<SessionSnapshot | null>(null);
	let maskLayer = $state<ReturnType<typeof MaskLayer> | undefined>();
	let maskBrush = $state(48);
	let maskErase = $state(false);
	let maskDirty = $state(false);
	let maskHistory = $state({ canUndo: false, canRedo: false });

	let presetNames = $state<Record<string, string>>({});
	let fieldsByTab = $state<Record<string, ImageField[]>>({});
	let refFieldsByTab = $state<Record<string, ReferenceField[]>>({});

	let sliceHistory = $state.raw<SliceHistory>(emptyHistory());
	let sliceSelected = $state<string[]>([]);
	let sliceTab = $state<'layout' | 'output'>('layout');
	let sliceAdding = $state(false);
	let sliceProgress = $state<{ done: number; total: number } | null>(null);
	let sliceSavedKey = $state<string | null>(null);
	let sliceItems = $state.raw<MediaToolItem[]>([]);
	let referencesOnly = $state(false);
	let detectWhenReady = $state(false);
	let collections = $state<{ value: string; label: string }[]>([]);
	let collectionsLoaded = false;
	let sliceRunId: string | null = null;
	let sliceThumbs = $state.raw<Map<string, string>>(new Map());
	const SLICE_COLLECTION_KEY = 'potionui_slice_collection';
	const SLICE_HINT_KEY = 'potionui_slice_hint_dismissed';
	let sliceSuggest = $state(false);
	let sliceHintGone = $state(false);

	onMount(() => {
		void loadFilterCatalog().catch(() => undefined);
		void loadMediaToolCatalog().catch(() => undefined);
		void loadPresetNameMap()
			.then((names) => (presetNames = names))
			.catch(() => undefined);
		if (!activeTool && source) activeTool = 'crop';
	});

	let catalog = $derived($mediaToolCatalog);
	let extensions = $derived(sharedExtensions($filterCatalog.ops, paintFilters.list()));
	let stillEffects = $derived(imageEffects(catalog));
	let railGroups = $derived.by<RailGroup[]>(() => {
		void $editorsRevision;
		const rail = imageRail(catalog);
		return source
			? rail
			: rail.map((group) => ({
					...group,
					tools: group.tools.map((tool) => ({ ...tool, disabled: true, reason: 'Open a picture first' }))
				}));
	});
	let registeredTool = $derived.by(() => {
		void $editorsRevision;
		return activeTool ? railTools.get('image', activeTool) : undefined;
	});
	let stackGroup = $derived.by(() => {
		if (!registeredTool?.children || !source) return null;
		if (paintActive && !paintState) return null;
		return { target: pendingHere?.target ?? null };
	});
	let rowInfoFor = $derived.by(() => {
		void $editorsRevision;
		void $runViews;
		void $mediaToolCatalogRevision;
		const steps = stack.steps;
		return (step: EditStep) => imageRowInfo(step, stepsBeneath(steps, steps.findIndex((entry) => entry.id === step.id)));
	});
	let applyOverride = $state<{ tool: string | null; label: string; enabled: boolean } | null>(null);
	ensureCoreImageEditor();
	ensureCoreEditorMenu();
	const toolHost = createImageToolHost({
		getStack: () => stack,
		setStack: (next) => (stack = next),
		getSource: () => ({ size: sourceSize, ref: resolvedRef }),
		render: async (stepsBeneath) => (await renderer.render(stepsBeneath === undefined ? effective : effective.slice(0, stepsBeneath))).canvas,
		setApply: (state) => (applyOverride = state ? { ...state, tool: activeTool } : null),
		writeLive: (next) => writeLive(next),
		settleLive: () => settleLive()
	});
	$effect(() => toolHost.syncPending(stack.pending));
	$effect(() => toolHost.syncStack(stack));
	let regionCache: { path: string; id: string } | null = null;
	const generate = createTabGenerate({
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: resolvedRef }),
		editLink: () => editLinkForSave(() => autosave.settle()),
		areaSize: () => get(maskArea),
		pictureSize: (id) => {
			const list = appliedSteps(stack);
			const at = id ? list.findIndex((step) => step.id === id) : -1;
			return outputSize(at >= 0 ? list.slice(0, at) : list, sourceSize);
		},
		stepsBeneath: async (id) => {
			let list = effective;
			const at = id ? list.findIndex((step) => step.id === id) : -1;
			if (at >= 0) list = list.slice(0, at);
			return renderer.payload(list);
		},
		landRegion: async () => {
			if (!(await landMask())) return null;
			const live = liveMaskNow;
			if (!live) return null;
			if (regionCache?.path === live.path) return { item_id: regionCache.id };
			const file = await canvasToPngFile(await canvasFromUrl(live.url), `${stem}-area.png`);
			const artifact = await uploadMediaToolArtifact(file);
			regionCache = { path: live.path, id: artifact.id };
			return { item_id: artifact.id };
		}
	});
	provideGenerateController(generate);
	const generateUi = generate.ui;
	$effect(() => generate.use(registeredTool?.task ?? null));
	$effect(() => {
		const current = stack;
		untrack(() => generate.resume(current));
	});
	onDestroy(() => generate.dispose());
	onMount(() => void generate.loadTasks());
	let effective = $derived(effectiveSteps(stack));
	let applied = $derived(appliedSteps(stack));
	let editCount = $derived(appliedCount(stack));
	let paintActive = $derived(isPaintEntry(activeTool));
	let toolKey = $derived(paintActive ? 'paint' : activeTool);
	let dirtyCount = $derived(unsavedEdits(stack, saved) + (paintActive && paintState?.dirty ? 1 : 0));
	let replace = $derived(replaceOption(origin));
	let fileName = $derived(source ? (source.name ?? mediaFileStem(source) ?? 'Picture') : null);
	let stem = $derived(mediaFileStem(source) ?? 'picture');
	let extension = $derived((fileName?.split('.').pop() ?? 'png').toUpperCase());
	let liveMaskNow = $derived(liveMask(mask, effective));
	let staleMask = $derived(maskIsStale(mask, effective));

	let pending = $derived(stack.pending);
	let stillEffectIds = $derived(new Set(imageEffectList(catalog).map((effect) => effect.id)));
	let effectCard = $derived(!!activeTool && stillEffectIds.has(activeTool) && !isLiveEntry(activeTool));
	let liveEntry = $derived(isLiveEntry(activeTool));
	let looksOrCard = $derived(activeTool === 'effects' || effectCard);
	let pendingHere = $derived(pending && pending.tool === (effectCard ? 'effects' : toolKey) ? pending : null);
	let entryLookup = $derived((op: string) => getOp(op, extensions));
	let appliedSet = $derived(appliedEntries(stack, entryLookup));
	let appliedList = $derived([...appliedSet].map((id) => (id === 'paint' ? paintEntryId('brush') : id)));
	let targetIndex = $derived(pending?.target ? effective.findIndex((step) => step.id === pending!.target) : -1);
	let stepsBeforeTarget = $derived(targetIndex >= 0 ? effective.slice(0, targetIndex) : effective);

	let mode = $derived<'paint' | 'mask' | 'crop' | 'preview'>(
		paintActive ? 'paint' : activeTool === 'mask' || registeredTool?.task ? 'mask' : activeTool === 'crop' ? 'crop' : 'preview'
	);
	let taskTool = $derived(!!registeredTool?.task);
	let settingsRef = $derived(settingsOf(registeredTool));
	let settingsFor = $state<string | null>(null);
	let settingsOpen = $derived(!!settingsRef && settingsFor !== null && settingsFor === activeTool);
	$effect(() => {
		const tool = activeTool;
		untrack(() => {
			if (settingsFor && settingsFor !== tool) settingsFor = null;
		});
	});
	let promptSettings = $derived(settingsRef ? { open: settingsOpen, toggle: () => (settingsFor = settingsOpen ? null : activeTool) } : undefined);
	$effect(() => {
		if (!taskTool) return;
		const before = get(maskToolState);
		chooseOverlay(LASSO_OVERLAY);
		return () => maskToolState.set(before);
	});
	let runStep = $derived.by(() => {
		const id = pending?.tool === 'generate' && pending.target ? pending.target : $generateUi.activeStepId;
		return id ? (generateSteps(stack).find((entry) => entry.step.id === id) ?? null) : null;
	});
	let runView = $derived(runStep ? ($runViews[runStep.params.run.id] ?? null) : null);
	let runStatus = $derived(runStep ? (runView?.status ?? runStep.params.run.status) : null);
	let runningId = $derived(runStep && (runStatus === 'queued' || runStatus === 'running') ? runStep.step.id : null);
	let largeArea = $derived(isLargeArea($maskArea, mainRender ? { width: mainRender.canvas.width, height: mainRender.canvas.height } : null, sourceSize));
	let saveNotices = $derived.by(() => {
		return [leftOutNotice(leftOutCount(applied)), outOfDateNotice(outOfDateCount(applied))].filter((line): line is string => !!line);
	});
	let runBeneath = $derived(runStep ? stepsBeneathOf(applied, runStep.step.id) : []);
	let runOutOfDate = $derived.by(() => {
		void $runViews;
		return runStep ? stepOutOfDate(runStep.step, runBeneath) : false;
	});
	let runOutline = $derived(runStep && runOutOfDate && shapeChange(runStep.params, runBeneath) === 'resize' ? runStep.params.rect : null);

	async function redoStep(id: string) {
		const outcome = await generate.redo(id);
		if (outcome.kind === 'area') await selectTool(outcome.task);
	}

	let cropParams = $derived<CropParams>((pendingHere?.tool === 'crop' ? (pendingHere.params as CropParams) : null) ?? { plan: EMPTY_IMAGE_PLAN, aspect: 'free' });
	let catalogTool = $derived(activeTool && !registeredTool?.options && !['crop', 'resize', 'paint', 'mask', 'effects', 'adjust', 'slice'].includes(activeTool) && !paintActive ? catalog.find((tool) => tool.id === (CATALOG_TOOL[activeTool!] ?? activeTool)) ?? null : null);
	let catalogValues = $derived<Record<string, unknown>>(
		pendingHere && catalogTool ? ((pendingHere.params as CatalogParams).values ?? {}) : catalogTool ? initialValues(catalogTool) : {}
	);
	let effectParams = $derived<EffectParams | null>(pendingHere?.tool === 'effects' ? (pendingHere.params as EffectParams) : null);
	let effectUpdating = $derived(pendingHere?.tool === 'effects' && !!pendingHere.target);
	let effectOriginal = $derived(effectUpdating ? ((stack.steps.find((step) => step.id === pendingHere?.target)?.params as EffectParams | undefined) ?? null) : null);

	let lookParams = $derived(effectParams?.kind === 'look' ? effectParams : null);
	let trayItems = $derived.by(() => {
		const needle = query.trim().toLowerCase();
		const visible = $filterCatalog.items.filter((item) => !isLocked(item, extensions) && (!needle || item.name.toLowerCase().includes(needle)));
		return buildStrip(visible, $filterCatalog.groups).flatMap((section) =>
			section.items.map((item) => ({
				id: item.id,
				label: item.name,
				group: section.label,
				hint: item.tags?.length ? item.tags[item.tags.length - 1] : null,
				selected: lookParams?.filter === item.id
			}))
		);
	});
	let trayById = $derived(new Map($filterCatalog.items.map((item) => [item.id, item] as const)));
	let lookContains = $derived(
			lookParams ? [...new Set((trayById.get(lookParams.filter)?.steps ?? []).map((step) => getOp(step.op, extensions)?.label).filter((label): label is string => !!label))] : []
		);
		let hoverItem = $derived<FilterItem | null>(activeTool === 'effects' && hoverLook && lookParams?.filter !== hoverLook ? (trayById.get(hoverLook) ?? null) : null);
	let hoverDraft = $derived(hoverItem ? (lookDraft({ kind: 'look', filter: hoverItem.id, name: hoverItem.name, intensity: hoverItem.intensity }) as StepDraft<unknown>) : null);
	let canCompareNow = $derived(mode === 'preview' && (liveEntry ? appliedSet.has(activeTool!) : !!pending));
	let mainSteps = $derived(mode === 'preview' ? previewWith(stack, hoverDraft) : mode === 'crop' ? (pendingHere?.tool === 'crop' ? stepsBeforeTarget : effective) : effective);
	let beforeList = $derived(
		mode === 'preview' && compare ? (liveEntry ? effective.filter((step) => entryOfStep(step) !== activeTool) : pending ? beforeSteps(stack) : []) : null
	);
	let thumbList = $derived(activeTool === 'effects' ? (pending ? beforeSteps(stack) : effective) : null);
	let paintList = $derived(mode === 'paint' ? (pendingHere?.tool === 'paint' ? stepsBeforeTarget : effective) : null);

	let appliedSlice = $derived(appliedSteps(stack).find(isSlice) ?? null);
	let sliceParams = $derived<SliceParams>(
		(pendingHere?.tool === 'slice' ? (pendingHere.params as SliceParams) : null) ??
			(activeTool === 'slice' && appliedSlice ? (appliedSlice.params as SliceParams) : null) ??
			{ ...emptySlice(stem), collection: collections.some((entry) => entry.value === storedCollection()) ? storedCollection() : null }
	);
	let sliceInStack = $derived(activeSlice(stack));
	let saveSlice = $derived.by<SliceParams | null>(() => {
		if (!sliceInStack) return null;
		const params = (sliceInStack.pending ? stack.pending!.params : sliceInStack.step!.params) as SliceParams;
		return kept(params.cells).length ? params : null;
	});
	let sliceCount = $derived(saveSlice ? kept(saveSlice.cells).length : 0);
	let sliceKey = $derived(saveSlice ? JSON.stringify([kept(saveSlice.cells), saveSlice.output, saveSlice.pattern, saveSlice.collection]) : null);
	let sliceSaved = $derived(!!sliceKey && sliceKey === sliceSavedKey);
	let sliceLogical = $derived(mainRender?.size ?? sourceSize ?? { width: 1, height: 1 });
	let sliceAnalysis = $derived.by<Analysis | null>(() => {
		if (activeTool !== 'slice' || !mainRender) return null;
		try {
			return analyseImage(mainRender.canvas, sliceParams.output.tolerance);
		} catch {
			return null;
		}
	});
	let sliceNumbers = $derived(numbering(sliceParams.cells));
	let sliceNames = $derived(outputNames(sliceParams.cells, sliceParams.pattern, sliceParams.sheet || stem));
	let sliceSizes = $derived(new Map(sliceParams.cells.map((cell) => [cell.id, cellOutputSize(sliceAnalysis, cell, sliceParams.output, sliceLogical)])));
	let sliceStates = $derived.by(() => {
		const map = new Map<string, 'done' | 'saving' | 'queued'>();
		const list = kept(sliceParams.cells);
		if (sliceProgress) list.forEach((cell, i) => map.set(cell.id, i < sliceProgress!.done ? 'done' : i === sliceProgress!.done ? 'saving' : 'queued'));
		else if (sliceSaved) list.forEach((cell) => map.set(cell.id, 'done'));
		return map;
	});
	let slicePadding = $derived.by(() => {
		if (sliceTab !== 'output' || !sliceParams.output.padding || !sliceSelected.length) return null;
		const map = new Map<string, Rect>();
		const px = sliceParams.output.padding / Math.max(1, sliceLogical.width);
		const py = sliceParams.output.padding / Math.max(1, sliceLogical.height);
		for (const id of sliceSelected) {
			const size = sliceSizes.get(id);
			if (size) map.set(id, { x0: size.rect.x0 - px, y0: size.rect.y0 - py, x1: size.rect.x1 + px, y1: size.rect.y1 + py });
		}
		return map;
	});
	let sliceNameRows = $derived<NameRow[]>(
		[...kept(sliceParams.cells), ...sliceParams.cells.filter((cell) => cell.skip)].map((cell) => ({
			id: cell.id,
			n: sliceNumbers.get(cell.id) ?? '—',
			name: sliceNames.get(cell.id) ?? '',
			typed: !!cell.name,
			thumb: sliceThumbs.get(cell.id) ?? null,
			skip: !!cell.skip
		}))
	);
	let sliceStrip = $derived<StripItem[]>(
		kept(sliceParams.cells).map((cell) => ({
			id: cell.id,
			n: sliceNumbers.get(cell.id) ?? '',
			thumb: sliceThumbs.get(cell.id) ?? null,
			width: sliceSizes.get(cell.id)?.width ?? 1,
			height: sliceSizes.get(cell.id)?.height ?? 1,
			state: sliceStates.get(cell.id) ?? null
		}))
	);
	let sliceSkipped = $derived(sliceParams.cells.filter((cell) => cell.skip).map((cell) => ({ id: cell.id, thumb: sliceThumbs.get(cell.id) ?? null })));
	let sliceDestination = $derived(collections.find((entry) => entry.value === saveSlice?.collection)?.label ?? 'Library');
	let sliceFill = $derived(sliceAnalysis && !sliceAnalysis.transparent ? { hex: hexColor(sliceAnalysis.bg), rgb: sliceAnalysis.bg.join(' ') } : null);
	let sliceLocked = $derived(!!sliceProgress);

	let sourceKey = $derived(source ? (source.item_id ?? source.generation_file_id ?? source.artifact_id ?? source.url ?? source.path ?? '') : '');
	let sheetPanels = $state(0);
	let suggestedPanels = $derived(sliceSuggest && !sliceHintGone && activeTool !== 'slice' && !phone && !effective.length && !pending && sheetPanels >= 3 ? sheetPanels : 0);

	$effect(() => {
		const url = sourceUrl;
		const key = sourceKey;
		if (!sliceSuggest || !url || untrack(() => dismissedHints()).includes(key)) return;
		let cancelled = false;
		loadImage(url)
			.then((image) => {
				if (!cancelled) sheetPanels = detectPanels(analyseImage(image)).length;
			})
			.catch(() => undefined);
		return () => {
			cancelled = true;
		};
	});

	let resizeInput = $derived(mode === 'preview' && activeTool === 'resize' ? (outputSize(pendingHere?.tool === 'resize' ? stepsBeforeTarget : effective, sourceSize) ?? sourceSize) : null);
	let resizeParams = $derived<ResizeParams | null>(
		resizeInput ? ((pendingHere?.tool === 'resize' ? (pendingHere.params as ResizeParams) : null) ?? { width: resizeInput.width, height: resizeInput.height, lock: true }) : null
	);

	let cropInput = $derived(mode === 'crop' && mainRender ? mainRender.size : null);
	let cropCanvas = $derived(mode === 'crop' && mainRender ? orient(mainRender.canvas, cropParams) : null);
	let cropFrame = $derived(cropInput ? displaySize(cropInput, cropParams.plan.rotation) : null);

	let shownSize = $derived(mode === 'crop' ? cropFrame : (mainRender?.size ?? null));
	let outputDims = $derived(outputSize(effective, sourceSize));

	let paintUndo = $derived(mode === 'paint' && paintState?.canUndo ? paintState : null);
	let sliceUndo = $derived(activeTool === 'slice' && sliceHistory.past.length ? sliceHistory.labels[sliceHistory.labels.length - 1] || 'slice edit' : null);
	let sliceRedo = $derived(activeTool === 'slice' && sliceHistory.future.length ? sliceHistory.futureLabels[0] || 'slice edit' : null);
	let maskUndo = $derived(mode === 'mask' && maskHistory.canUndo);
	let undoText = $derived(paintUndo ? (paintUndo.undoLabel ?? 'stroke') : maskUndo ? 'marked area' : (sliceUndo ?? undoLabel(stack)));
		let canUndoNow = $derived(!!paintUndo || !!sliceUndo || maskUndo || canUndo(stack));
	let canRedoNow = $derived(mode === 'paint' && paintState?.canRedo ? true : (mode === 'mask' && maskHistory.canRedo) || !!sliceRedo || canRedo(stack));
	let redoText = $derived(mode === 'paint' && paintState?.canRedo ? (paintState.redoLabel ?? 'stroke') : (sliceRedo ?? redoLabel(stack)));
	let canSave = $derived(
		!!source && !!resolvedRef && !saving && !busy && (referencesOnly || !(saveSlice && sliceSaved)) && (effective.length > 0 || !!pending || mode === 'paint')
	);

	let generationTabs = $derived(userGenerationTabs($tabsStore.tabs));
	let tabFormKey = $derived(generationTabs.map((tab) => `${tab.id}|${tab.selectedPreset}|${tab.selectedMode}|${tab.selectedVariant ?? ''}`).join(','));
	let referenceTargets = $derived<ReferenceTarget[]>(referenceTargetsFor(generationTabs, refFieldsByTab, presetNames, sliceCount || sliceItems.length));
	let sendTargets = $derived(sendTargetsFor(generationTabs, fieldsByTab, presetNames, areaIsMarked({ marking: mode === 'mask', drawn: !!$maskArea, savedUsable: !!liveMaskNow }), sendFirst));

	$effect(() => {
		const ref = source;
		const url = sourceUrl;
		sourceError = null;
		if (!ref || !url) {
			sourceSize = null;
			resolvedRef = null;
			origin = 'unknown';
			return;
		}
		let cancelled = false;
		const direct = inputRef(ref);
		resolvedRef = direct;
		origin = sourceOrigin(ref);
		loadImage(url)
			.then((image) => {
				if (!cancelled) sourceSize = { width: image.naturalWidth, height: image.naturalHeight };
			})
			.catch(() => {
				if (!cancelled) sourceError = 'This picture could not be opened.';
			});
		const path = ref.relative_path || ref.path;
		if (!direct && path) {
			void resolveEditableResource(null, path, 'image').then((found) => {
				if (cancelled) return;
				if (found.itemId) {
					resolvedRef = { item_id: found.itemId };
					origin = 'library';
				} else if (found.generationFileId) {
					resolvedRef = { generation_file_id: found.generationFileId };
					origin = 'history';
				}
			});
		}
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const ref = source;
		untrack(() => {
			if (!ref) return;
			const title = mediaFileStem(ref);
			if (title) host.setTitle(title);
			if (ref.url) host.setThumbnail(ref.url);
			if (!activeTool) activeTool = 'crop';
		});
	});

	$effect(() => {
		if (!canCompareNow && compare) compare = false;
	});

	$effect(() => {
		const canvas = thumbRender?.canvas ?? null;
		const size = phone ? 112 : 144;
		thumbSource = thumbPainter.sourceFrom(canvas, size);
	});

	$effect(() => {
		if (activeTool !== 'effects' && hoverLook) hoverLook = null;
	});

	onDestroy(() => {
		if (liveTimer) clearTimeout(liveTimer);
	});

	$effect(() => {
		const next = sliceLast(stack);
		if (next !== stack) stack = next;
	});

	$effect(() => {
		if (!saveMenuOpen && referencesOnly) referencesOnly = false;
	});

	$effect(() => {
		if (activeTool !== 'slice' || collectionsLoaded) return;
		collectionsLoaded = true;
		void api
			.listCollections('library')
			.then((response) => {
				if (response.success && response.data) collections = response.data.collections.map((entry) => ({ value: entry.id, label: entry.name }));
			})
			.catch(() => undefined);
	});

	$effect(() => {
		if (!detectWhenReady || activeTool !== 'slice' || !sliceAnalysis) return;
		untrack(() => {
			detectWhenReady = false;
			runDetect();
		});
	});

	$effect(() => {
		const canvas = mainRender?.canvas ?? null;
		const an = sliceAnalysis;
		const cells = sliceParams.cells;
		const output = sliceParams.output;
		const logical = sliceLogical;
		if (activeTool !== 'slice' || !canvas || !cells.length) return;
		const timer = setTimeout(() => {
			const next = new Map<string, string>();
			for (const cell of cells) {
				try {
					next.set(cell.id, sliceCrop(canvas, an, cell, output, logical, 160).toDataURL('image/png'));
				} catch {
					continue;
				}
			}
			sliceThumbs = next;
		}, 120);
		return () => clearTimeout(timer);
	});

	$effect(() => {
		const wanted = knownTool(host.activeTool);
		untrack(() => {
			if (wanted && wanted !== activeTool) void selectTool(wanted);
		});
	});

	$effect(() => {
		const hint = (host as HintedHost).hint;
		const ready = !!sourceSize;
		if (!hint || !ready) return;
		untrack(() => void consumeHint(hint));
	});

	async function consumeHint(hint: ImageHint) {
		(host as HintedHost).clearHint?.();
		if (hint.sendTo) sendFirst = hint.sendTo;
		if (hint.suggestSlice) sliceSuggest = true;
		if (hint.detect) {
			if (activeTool !== 'slice') await selectTool('slice');
			detectWhenReady = true;
			return;
		}
		if (hint.orientation) {
			if (activeTool !== 'crop') await selectTool('crop');
			const plan = hint.orientation === 'rotate' ? { ...EMPTY_IMAGE_PLAN, rotation: 90 as const } : { ...EMPTY_IMAGE_PLAN, flipHorizontal: true };
			setDraft('crop', { plan, aspect: 'free' } satisfies CropParams);
			return;
		}
		if (hint.look) {
			if (activeTool !== 'effects') await selectTool('effects');
			await loadFilterCatalog();
			const item = get(filterCatalog).items.find((candidate) => candidate.id === hint.look);
			if (item) setDraft('effects', { kind: 'look', filter: item.id, name: item.name, intensity: item.intensity } satisfies EffectParams);
			return;
		}
		const hinted = hint.effect ? OP_TO_ENTRY[hint.effect] : null;
		if (hinted && activeTool !== hinted) await selectTool(hinted);
	}

	let writeTimer: ReturnType<typeof setTimeout> | null = null;
	let lastWritten = JSON.stringify(writeImageState(initial));
	let pendingWrite: Record<string, unknown> | null = null;
	let currentPayload: Record<string, unknown> = writeImageState(initial);
	let autosaveStatus = $state<AutosaveStatus>('idle');
	let autosaveBusy = $state(false);

	const autosave = createEditAutosave({
		tabId: untrack(() => host.tabId),
		edits: {
			create: (body) => api.createEdit(body),
			get: (id) => api.getEdit(id),
			save: (id, body) => api.saveEdit(id, body)
		},
		outbox: editAutosaveOutbox,
		initialState: currentPayload,
		readLink: () => readEditLink(host.state),
		source: () => editSourceOf(host.source),
		onLink: (link) => host.setState(withEditLink(currentPayload, link)),
		onStatus: (next) => (autosaveStatus = next),
		onRestore: applyServerState
	});

	function applyServerState(raw: Record<string, unknown>): Record<string, unknown> {
		const next = readImageState(raw);
		stack = sliceLast(next.stack);
		mask = next.mask;
		saved = next.saved;
		const payload = writeImageState({ stack, activeTool, viewport, mask, saved });
		currentPayload = payload;
		lastWritten = JSON.stringify(payload);
		lastContent = JSON.stringify(writeImageState({ stack, activeTool, viewport: fitViewport(), mask, saved }));
		if (writeTimer) clearTimeout(writeTimer);
		writeTimer = null;
		pendingWrite = null;
		return payload;
	}

	async function loadNewerEdit() {
		autosaveBusy = true;
		const ok = await autosave.loadNewer();
		autosaveBusy = false;
		if (!ok) toasts.error('Could not load the newer version. Try again.');
	}

	async function keepMineAsCopy() {
		autosaveBusy = true;
		const ok = await autosave.keepMine();
		autosaveBusy = false;
		if (ok) toasts.success('Your version is saved as a copy.');
		else toasts.error('Could not save your version as a copy. Try again.');
	}

	onMount(() => {
		const ids = get(tabsStore).tabs.map((tab) => tab.id);
		if (ids.includes(host.tabId)) editAutosaveOutbox.prune(ids);
		autosave.start();
	});

	onDestroy(() => void autosave.close());

	function flushWrite() {
		if (writeTimer) clearTimeout(writeTimer);
		writeTimer = null;
		if (!pendingWrite) return;
		const next = pendingWrite;
		pendingWrite = null;
		host.setState(withEditLink(next, autosave.link()));
	}

	let lastContent = JSON.stringify(writeImageState({ ...initial, viewport: fitViewport() }));

	$effect(() => {
		const payload = writeImageState({ stack, activeTool, viewport, mask, saved });
		const json = JSON.stringify(payload);
		if (json === lastWritten) return;
		lastWritten = json;
		const content = JSON.stringify(writeImageState({ stack, activeTool, viewport: fitViewport(), mask, saved }));
		const viewOnly = content === lastContent;
		lastContent = content;
		untrack(() => {
			pendingWrite = payload;
			currentPayload = payload;
			autosave.observe(payload);
			if (writeTimer) clearTimeout(writeTimer);
			if (viewOnly) writeTimer = setTimeout(flushWrite, 150);
			else flushWrite();
		});
	});

	onDestroy(() => untrack(flushWrite));

	let lastDirty = -1;
	$effect(() => {
		const count = dirtyCount;
		if (count === lastDirty) return;
		const first = lastDirty === -1;
		lastDirty = count;
		if (first && count === 0) return;
		untrack(() => host.setDirty(count));
	});

	$effect(() => {
		if (!source) {
			untrack(() => host.setActions({ hidden: true }));
			return;
		}
		const actions = {
			menu: { run: () => (phoneMenuOpen = true) },
			undo: { label: undoText ? `Undo ${undoText}` : 'Undo', disabled: !canUndoNow, run: doUndo },
			redo: { label: redoText ? `Redo ${redoText}` : 'Redo', disabled: !canRedoNow, run: doRedo },
			save: { label: saveSlice ? (sliceSaved ? 'Saved' : `Save ${sliceCount}`) : 'Save', disabled: !canSave, run: () => (saveMenuOpen = true) }
		};
		untrack(() => host.setActions(actions));
	});

	$effect(() => {
		void tabFormKey;
		const tabs = untrack(() => generationTabs);
		for (const tab of tabs) {
			if (!tab.selectedPreset || !tab.selectedMode) continue;
			const id = tab.id;
			loadPresetFormSchema(tab.selectedPreset, tab.selectedMode, tab.selectedVariant ?? undefined)
				.then((schema) => {
					fieldsByTab = { ...fieldsByTab, [id]: imageFieldsOf(schema) };
					refFieldsByTab = { ...refFieldsByTab, [id]: referenceFieldsOf(schema) };
				})
				.catch(() => undefined);
		}
	});

	function renderKey(steps: readonly EditStep[], maxSide: number): string {
		return JSON.stringify([sourceUrl, resolvedRef, maxSide, stepsKey(steps)]);
	}

	function cachedRender(steps: readonly EditStep[], maxSide: number | undefined, signal: AbortSignal): Promise<Rendered> {
		const key = renderKey(steps, maxSide ?? 0);
		let found = renders.get(key);
		if (!found) {
			const created = renderer.render(steps, maxSide ? { maxSide, signal } : { signal });
			created.catch(() => {
				if (renders.get(key) === created) renders.delete(key);
			});
			renders.set(key, created);
			if (renders.size > 12) renders.delete(renders.keys().next().value!);
			found = created;
		}
		return found;
	}

	function track(list: readonly EditStep[] | null, assign: (value: Rendered | null) => void, maxSide?: number, primary = false) {
		void surfaceVersion;
		void $editorsRevision;
		const size = sourceSize;
		const url = sourceUrl;
		const ref = resolvedRef;
		if (!list || !size || !url) {
			assign(null);
			return;
		}
		renderer.setSource({ url, size, ref });
		const controller = new AbortController();
		if (primary) rendering = true;
		const run = (retries: number) => {
			cachedRender(list, maxSide, controller.signal)
				.then((value) => {
					if (controller.signal.aborted) return;
					assign(value);
					if (primary) renderError = null;
					if (primary) rendering = false;
				})
				.catch((error: unknown) => {
					if (controller.signal.aborted) return;
					if (isAbort(error) && retries > 0) {
						run(retries - 1);
						return;
					}
					if (primary) {
						renderError = describeMediaToolFailure(error, 'The preview could not be drawn.');
						rendering = false;
					}
				});
		};
		const timer = setTimeout(() => run(2), 60);
		return () => {
			controller.abort();
			clearTimeout(timer);
		};
	}

	$effect(() => track(mode === 'paint' ? null : mainSteps, (value) => (mainRender = value), undefined, true));
	$effect(() => track(beforeList, (value) => (beforeRender = value)));
	$effect(() => track(thumbList, (value) => (thumbRender = value)));
	$effect(() => track(paintList, (value) => (paintBase = value), 4096));

	function recoverSurfaces() {
		renders.clear();
		renderer.reset();
		surfaceVersion += 1;
	}

	function draftFor(tool: string, params: unknown): StepDraft<unknown> | null {
		if (tool === 'crop') {
			const crop = params as CropParams;
			const input = cropInput ?? sourceSize;
			if (!input) return null;
			const plan = crop.plan;
			const noop = plan.rotation === 0 && !plan.flipHorizontal && !plan.flipVertical && plan.targetLongestSide === null && plan.rect.x <= 0.0005 && plan.rect.y <= 0.0005 && plan.rect.width >= 0.9995 && plan.rect.height >= 0.9995;
			if (noop && !pending?.target) return null;
			return cropDraft(crop, aspectLabel(crop.aspect), planOutputSize(plan, input)) as StepDraft<unknown>;
		}
		if (tool === 'resize') return resizeDraft(params as ResizeParams) as StepDraft<unknown>;
		if (tool === 'slice') {
			const slice = params as SliceParams;
			if (!slice.cells.length && !pending?.target) return null;
			return sliceDraft(slice) as StepDraft<unknown>;
		}
		if (tool === 'effects') {
			const effect = params as EffectParams;
			if (effect.kind === 'look') return lookDraft(effect) as StepDraft<unknown>;
			if (effect.kind === 'effect') return toolEffectDraft(effect, catalog) as StepDraft<unknown>;
			return adjustDraft(effect) as StepDraft<unknown>;
		}
		return catalogDraft(tool, (params as CatalogParams).values, catalog) as StepDraft<unknown>;
	}

	function openEffect(id: string): boolean {
		const next = reopenLatest(stack, (step) => step.tool === 'effects' && (step.params as EffectParams).kind === 'effect' && (step.params as ToolEffectParams).tool === id);
		if (next !== stack) {
			notice = null;
			stack = next;
			return true;
		}
		if (stack.pending?.target) stack = discardPending(stack);
		return false;
	}

	function setDraft(tool: string, params: unknown) {
		notice = null;
		const next = draftFor(tool, params);
		if (!next) {
			if (pending && !pending.target) stack = discardPending(stack);
			return;
		}
		if (pending && pending.tool === tool) stack = updatePending(stack, next);
		else stack = beginEdit(stack, next);
	}

	function settleLive() {
		if (liveTimer) clearTimeout(liveTimer);
		liveTimer = null;
		if (isLive(stack)) stack = commitLive(stack);
	}

	function writeLive(next: EditStackState<unknown>) {
		notice = null;
		stack = next;
		if (liveTimer) clearTimeout(liveTimer);
		liveTimer = setTimeout(settleLive, LIVE_COMMIT_MS);
	}

	function pickLook(id: string | null) {
		hoverLook = null;
		if (id === null) {
			if (pending?.tool === 'effects' && lookParams) stack = discardPending(stack);
			return;
		}
		const item = trayById.get(id);
		if (!item) return;
		const intensity = lookParams?.filter === item.id ? lookParams.intensity : item.intensity;
		setDraft('effects', { kind: 'look', filter: item.id, name: item.name, intensity } satisfies EffectParams);
	}

	async function landPaint(): Promise<boolean> {
		if (!paintStage) return true;
		paintSession?.placeFloating();
		const target = pending?.tool === 'paint' ? pending.target : null;
		busy = true;
		try {
			const result = await paintStage.exportLayer();
			if (!result) {
				if (target) stack = remove(stack, target);
				else if (pending?.tool === 'paint') stack = discardPending(stack);
				return true;
			}
			const artifact = await uploadMediaToolArtifact(result.file);
			const params: PaintParams = { layer: artifact.id, url: artifact.url, layers: result.layers };
			const next = paintDraft(params, outputSize(paintList ?? effective, sourceSize) ?? result.size) as StepDraft<unknown>;
			stack = target ? apply(updatePending(stack, next)) : apply(beginEdit(stack, next));
			return true;
		} catch (error) {
			notice = describeMediaToolFailure(error);
			return false;
		} finally {
			busy = false;
		}
	}

	async function landMask(): Promise<boolean> {
		if (!maskLayer || !maskDirty) return true;
		busy = true;
		try {
			if (!maskLayer.hasStrokes()) {
				mask = null;
				maskDirty = false;
				return true;
			}
			const file = await maskLayer.exportMask(`${stem}-mask.png`);
			if (!file) return false;
			const stored = await api.uploadMedia(file, 'derived_artifact');
			if (!stored.success || !stored.data) throw new Error(stored.message || 'The mask could not be saved');
			mask = maskChannelOf(stored.data, geometryKey(effective));
			maskDirty = false;
			return true;
		} catch (error) {
			notice = describeMediaToolFailure(error);
			return false;
		} finally {
			busy = false;
		}
	}

	function landSlice() {
		if (pending?.tool !== 'slice') return;
		if ((pending.params as SliceParams).cells.length) return;
		stack = pending.target ? remove(discardPending(stack), pending.target) : discardPending(stack);
	}

	async function leaveTool(): Promise<boolean> {
		settleLive();
		hoverLook = null;
		if (mode === 'paint') return landPaint();
		if (mode === 'mask') return landMask();
		landSlice();
		sliceAdding = false;
		if (stack.pending) stack = sliceLast(apply(stack));
		return true;
	}

	async function selectTool(requested: string) {
		const id = canonicalTool(requested);
		if (!source || busy) return;
		if (id === activeTool && !(pending && pending.tool !== toolKeyOf(id))) return;
		if (paintActive && isPaintEntry(id) && paintSession && mode === 'paint') {
			paintSession.setTool(paintToolOfEntry(id));
			activeTool = id;
			host.setActiveTool(id);
			return;
		}
		if (!(await leaveTool())) return;
		notice = null;
		compare = false;
		activeTool = id;
		host.setActiveTool(id);
		const existing = id === 'slice' ? appliedSteps(stack).find(isSlice) : null;
		if (existing) stack = reopen(stack, existing.id);
		if (stillEffectIds.has(id) && !isLiveEntry(id) && !openEffect(id)) {
			const effect = imageEffectList(catalog).find((candidate) => candidate.id === id);
			if (effect) setDraft('effects', { kind: 'effect', tool: id, name: effect.label, values: effectDefaults(effect) } satisfies ToolEffectParams);
		}
	}

	function toolKeyOf(id: string): string {
		return isPaintEntry(id) ? 'paint' : id;
	}

	function closeActiveTool() {
		if (pending) stack = discardPending(stack);
		activeTool = null;
		host.setActiveTool(null);
	}

	async function reopenStep(id: string) {
		const step = stack.steps.find((candidate) => candidate.id === id);
		if (!step || busy) return;
		if (pending?.target === id) return;
		if (!(await leaveTool())) return;
		const kind = stepKinds.get('image', step.tool);
		const tool = step.tool === 'effects' ? (entryOfStep(step) ?? 'effects') : ((kind && reopenToolOf(kind, step.params)) ?? step.tool);
		if (!isRailTool(tool, catalog)) return;
		if (!isLiveEntry(tool)) stack = reopen(stack, id);
		const entry = canonicalTool(tool);
		activeTool = entry;
		host.setActiveTool(entry);
	}

	async function removeAllEdits() {
		const next = await confirmRemoveAll(stack);
		if (!next) return;
		generate.allRemoved(stack);
		stack = next;
	}

	function doUndo() {
		if (paintUndo && paintSession) {
			paintSession.undo();
			return;
		}
		if (maskUndo && maskLayer?.undo()) return;
		if (sliceUndo && !sliceLocked) {
			const back = undoSlice(sliceHistory, sliceParams);
			if (back) {
				sliceHistory = back.history;
				writeSlice(back.params);
				return;
			}
		}
		stack = undo(stack);
	}

	function doRedo() {
		if (mode === 'paint' && paintState?.canRedo && paintSession) {
			paintSession.redo();
			return;
		}
		if (mode === 'mask' && maskHistory.canRedo && maskLayer?.redo()) return;
		if (sliceRedo && !sliceLocked) {
			const forward = redoSlice(sliceHistory, sliceParams);
			if (forward) {
				sliceHistory = forward.history;
				writeSlice(forward.params);
				return;
			}
		}
		stack = redo(stack);
	}

	function writeSlice(next: SliceParams) {
		notice = null;
		if (!pending && appliedSlice) stack = reopen(stack, appliedSlice.id);
		setDraft('slice', next);
	}

	function setSlice(next: SliceParams, label?: string) {
		if (sliceLocked) return;
		if (label) sliceHistory = record(sliceHistory, sliceParams, label);
		writeSlice({ ...next, sheet: next.sheet || stem });
	}

	function setCells(cells: Cell[], label?: string) {
		setSlice({ ...sliceParams, cells }, label);
	}

	function setGrid(cols: number, rows: number) {
		const c = Math.max(1, Math.min(12, Math.round(cols)));
		const r = Math.max(1, Math.min(12, Math.round(rows)));
		sliceSelected = [];
		setSlice({ ...sliceParams, cells: gridCells(sliceAnalysis, c, r, sliceParams.layout.snap), layout: { ...sliceParams.layout, preset: `${c}x${r}`, cols: c, rows: r, detected: null } }, 'grid');
	}

	function runDetect() {
		if (!sliceAnalysis) {
			detectWhenReady = true;
			return;
		}
		const result = detectCells(sliceAnalysis, sliceParams.layout.preset ? sliceParams.cells : []);
		sliceSelected = [];
		const merged = result.cells.find((cell) => cell.merged);
		if (merged) sliceSelected = [merged.id];
		setSlice({ ...sliceParams, cells: result.cells, layout: { ...sliceParams.layout, preset: null, detected: result.summary } }, 'detect');
	}

	function sliceCellAction(kind: 'split' | 'skip' | 'delete' | 'merge') {
		const cells = sliceParams.cells;
		const id = sliceSelected[0];
		if (kind === 'merge' && sliceSelected.length > 1) {
			setCells(mergeCells(cells, sliceSelected), 'merge');
			sliceSelected = [sliceSelected[0]];
		} else if (!id) return;
		else if (kind === 'split') {
			setCells(splitCell(cells, id, sliceAnalysis), 'split');
			sliceSelected = [];
		} else if (kind === 'skip') setCells(toggleSkip(cells, id), 'skip');
		else if (kind === 'delete') {
			setCells(deleteCell(cells, id), 'delete');
			sliceSelected = [];
		}
	}

	function dismissedHints(): string[] {
		try {
			const parsed = JSON.parse(localStorage.getItem(SLICE_HINT_KEY) ?? '[]');
			return Array.isArray(parsed) ? parsed : [];
		} catch {
			return [];
		}
	}

	function dismissSliceHint() {
		sliceHintGone = true;
		try {
			localStorage.setItem(SLICE_HINT_KEY, JSON.stringify([...dismissedHints().filter((key) => key !== sourceKey), sourceKey].slice(-200)));
		} catch {
			return;
		}
	}

	async function acceptSliceHint() {
		sliceHintGone = true;
		await selectTool('slice');
		runDetect();
	}

	function storedCollection(): string | null {
		try {
			return localStorage.getItem(SLICE_COLLECTION_KEY);
		} catch {
			return null;
		}
	}

	function rememberCollection(id: string | null) {
		try {
			if (id) localStorage.setItem(SLICE_COLLECTION_KEY, id);
		} catch {
			return;
		}
	}

	async function applyTool() {
		if (busy || !source) return;
		if (mode === 'paint') {
			if (await landPaint()) paintKey += 1;
			return;
		}
		if (mode === 'mask') {
			await landMask();
			return;
		}
		if (!pending && catalogTool && activeTool) stack = beginEdit(stack, catalogDraft(activeTool, catalogValues, catalog) as StepDraft<unknown>);
		landSlice();
		sliceAdding = false;
		if (stack.pending) stack = sliceLast(apply(stack));
	}

	function resetTool() {
		notice = null;
		if (liveEntry && activeTool) {
			settleLive();
			stack = resetEntry(stack, activeTool, entryLookup);
			return;
		}
		if (looksOrCard && effectParams) {
			setDraft('effects', effectDefaultsOf(effectParams, $filterCatalog.items, stillEffects));
			return;
		}
		if (mode === 'mask') {
			maskLayer?.clear();
			maskDirty = true;
			return;
		}
		if (mode === 'paint') {
			paintKey += 1;
			if (pending?.tool === 'paint' && !pending.target) stack = discardPending(stack);
			return;
		}
		if (activeTool === 'slice') {
			sliceSelected = [];
			sliceAdding = false;
			if (pending?.tool === 'slice') sliceHistory = record(sliceHistory, sliceParams, 'reset');
		}
		if (pending) stack = discardPending(stack);
	}

	let paintKey = $state(0);

	function enterKey() {
		if (mode === 'paint' && paintSession?.floatingLayer) {
			paintSession.placeFloating();
			return;
		}
		return applyTool();
	}

	$effect(() => {
		if (mode === 'paint') return;
		untrack(() => {
			paintSession = null;
			paintState = null;
		});
	});

	$effect(() => {
		const toolId = paintState?.toolId;
		if (!toolId || !paintActive) return;
		untrack(() => {
			const entry = paintEntryId(toolId);
			if (entry === activeTool || !railTools.get('image', entry)) return;
			activeTool = entry;
			host.setActiveTool(entry);
		});
	});

	function onPaintKey(event: KeyboardEvent) {
		if (!source || busy || !pageOwnsKeys(event)) return;
		const entries = railGroups.flatMap((group) => group.tools.filter((tool) => isPaintEntry(tool.id) && !tool.disabled && tool.keys).map((tool) => ({ id: tool.id, key: tool.keys })));
		const id = paintToolForKey(event, entries);
		if (!id) return;
		event.preventDefault();
		event.stopPropagation();
		void selectTool(id);
	}

	async function prepareSave(): Promise<boolean> {
		if (!(await leaveTool())) return false;
		if (mode === 'paint') paintKey += 1;
		return true;
	}

	function itemName(item: MediaToolItem | undefined): string {
		return item ? (item.original_filename ?? item.filename) : 'The copy';
	}

	async function runSave(saveMode: 'new' | 'update' | 'replace') {
		const ref = resolvedRef;
		if (!ref || saving) return;
		if (!(await prepareSave())) return;
		const steps = withoutSlice(appliedSteps(stack));
		if (!steps.some((step) => !step.hidden)) {
			notice = 'There are no visible edits to save.';
			return;
		}
		saving = { percent: 0, label: 'Saving' };
		let stop: (() => void) | null = null;
		try {
			const [payload, editLink] = await Promise.all([renderer.payload(steps), editLinkForSave(() => autosave.settle())]);
			const handle = await mediaToolRuns.start({
				keys: [`tool-tab:${host.tabId}`],
				toolId: payload.find((step) => !step.hidden)?.tool ?? 'edit',
				label: 'Image edit',
				body: runBody(payload, ref, saveMode, saved?.itemId ?? null, null, editLink)
			});
			host.setRun({ runId: handle.runId, progress: 0 });
			stop = mediaToolRuns.subscribe((runs) => {
				const run = Object.values(runs).find((entry) => entry.runId === handle.runId);
				if (run) saving = { percent: run.progress * 100, label: 'Saving' };
			});
			const outcome = await handle.done;
			if (outcome.status !== 'completed') {
				announceOutcome(outcome);
				return;
			}
			const signature = stackSignature(stack);
			if (saveMode === 'replace') {
				stack = createStack();
				saved = null;
				mask = null;
				sourceVersion += 1;
				renders.clear();
				renderer.reset();
				announceOutcome(outcome);
			} else if (saveMode === 'update') {
				saved = { itemId: saved?.itemId ?? outcome.items[0]?.id ?? '', signature };
				toasts.show('success', `${itemName(outcome.items[0])} was updated.`, { title: 'Saved' });
			} else {
				const item = outcome.items[0];
				if (item) saved = { itemId: item.id, signature };
				if (origin === 'history') {
					landOnHistoryResult(outcome.items, { source: null, startPath: '/generate', env: { overlayOpen: false, typing: false, samePage: false } });
				} else {
					announceOutcome(outcome);
				}
			}
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Save failed' });
		} finally {
			stop?.();
			saving = null;
			host.setRun(null);
		}
	}

	function newReferencesTab(items: readonly MediaToolItem[]) {
		if (!items.length) return;
		handOffToNewTab(items, `${stem} refs`, {
			tabs: tabsStore,
			loadSchema: loadPresetFormSchema,
			oncommit: (tab, field, takes) =>
				toasts.show('success', `${takes} ${takes === 1 ? 'image' : 'images'} in ${field.title}, ${tab.name}.`, { title: 'Sent as references' })
		});
		toasts.show('info', `Pick a preset with a references field. The ${items.length === 1 ? 'image goes' : `${items.length} images go`} into it.`, { title: 'New generation tab' });
	}

	async function runSliceSave(target: ReferenceTarget | 'new-tab' | null = null) {
		const ref = resolvedRef;
		if (!ref || saving) return;
		if (!(await prepareSave())) return;
		const steps = appliedSteps(stack);
		const step = steps.find((candidate) => isSlice(candidate) && !candidate.hidden);
		const params = step ? (step.params as SliceParams) : null;
		const total = params ? kept(params.cells).length : 0;
		if (!params || !total) return;
		const key = JSON.stringify([kept(params.cells), params.output, params.pattern, params.collection]);
		const caption = `Saving ${total} ${total === 1 ? 'image' : 'images'}`;
		saving = { percent: 0, label: caption, caption };
		sliceProgress = { done: 0, total };
		let stop: (() => void) | null = null;
		try {
			const payload = await renderer.payload(steps);
			const handle = await mediaToolRuns.start({
				keys: [`tool-tab:${host.tabId}`],
				toolId: 'slice',
				label: 'Slice',
				body: runBody(payload, ref, 'new', null, params.collection)
			});
			sliceRunId = handle.runId;
			host.setRun({ runId: handle.runId, progress: 0 });
			stop = mediaToolRuns.subscribe((runs) => {
				const run = Object.values(runs).find((entry) => entry.runId === handle.runId);
				if (!run) return;
				saving = { percent: run.progress * 100, label: caption, caption };
				sliceProgress = { done: Math.min(total, Math.floor(run.progress * total)), total };
				host.setRun({ runId: handle.runId, progress: run.progress * 100 });
			});
			const outcome = await handle.done;
			if (outcome.status !== 'completed') {
				announceOutcome(outcome);
				return;
			}
			sliceItems = outcome.items;
			sliceSavedKey = key;
			rememberCollection(params.collection);
			if (target === 'new-tab') {
				newReferencesTab(outcome.items);
				return;
			}
			if (target) {
				commitReferences(target, outcome.items);
				return;
			}
			const where = collections.find((entry) => entry.value === params.collection)?.label;
			const count = outcome.items.length;
			toasts.show('success', where ? `Filed in ${where}. The sheet is kept.` : 'The sheet is kept.', {
				title: `Saved ${count} ${count === 1 ? 'image' : 'images'} to Library`,
				duration: 8000,
				action: { label: 'Open in Library', onClick: () => void goto('/library') },
				actions: [{ label: 'Use as references', onClick: useAsReferences }]
			});
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Save failed' });
		} finally {
			stop?.();
			saving = null;
			sliceProgress = null;
			sliceRunId = null;
			host.setRun(null);
		}
	}

	function cancelSave() {
		if (sliceRunId) void mediaToolRuns.cancelRun(sliceRunId);
	}

	function useAsReferences() {
		if (!sliceItems.length) return;
		const targets = referenceTargetsFor(userGenerationTabs(get(tabsStore).tabs), refFieldsByTab, presetNames, sliceItems.length);
		if (targets.length === 1) {
			commitReferences(targets[0], sliceItems);
			return;
		}
		if (!targets.length) {
			toasts.show('info', 'Open a generation tab with a references field, then try again.', { title: 'No references field' });
			return;
		}
		referencesOnly = true;
		saveMenuOpen = true;
	}

	function commitReferences(target: ReferenceTarget, items: readonly MediaToolItem[]) {
		const tab = get(tabsStore).tabs.find((candidate): candidate is GenerationTab => candidate.id === target.tabId && isGenerationTab(candidate));
		if (!tab) {
			toasts.show('error', 'That generation tab is closed.', { title: 'Send failed' });
			return;
		}
		const fresh = referenceTargetsFor([tab], refFieldsByTab, presetNames, items.length).find((entry) => entry.id === target.id);
		const takes = fresh ? fresh.takes : 0;
		if (!takes) {
			toasts.show('error', `${target.field.title} has no free slots.`, { title: 'Send failed' });
			return;
		}
		tabsStore.updateTab(tab.id, {
			formData: { ...tab.formData, [target.field.name]: referencePatch(tab.formData?.[target.field.name], items, takes) },
			formReloadGeneration: (tab.formReloadGeneration ?? 0) + 1
		});
		untrack(flushWrite);
		tabsStore.setActiveTab(tab.id);
		const note = takes < items.length ? ` The first ${takes} went in; all ${items.length} are in Library.` : '';
		toasts.show('success', `${takes} ${takes === 1 ? 'image' : 'images'} in ${target.field.title}, ${tab.name}.${note}`, { title: 'Sent as references' });
	}

	async function downloadSlices() {
		if (!(await prepareSave())) return;
		const params = saveSlice;
		if (!params) return;
		busy = true;
		try {
			const rendered = await renderFull();
			const an = analyseImage(rendered.canvas, params.output.tolerance);
			const names = outputNames(params.cells, params.pattern, params.sheet || stem);
			const files: { name: string; data: Uint8Array }[] = [];
			const used = new Set<string>();
			for (const cell of kept(params.cells)) {
				const canvas = sliceCrop(rendered.canvas, an, cell, params.output, rendered.size, FULL_SIDE);
				const file = await canvasToPngFile(canvas, 'cell.png');
				let name = names.get(cell.id) ?? cell.id;
				for (let n = 2; used.has(name); n++) name = `${names.get(cell.id)}-${n}`;
				used.add(name);
				files.push({ name: `${name}.png`, data: new Uint8Array(await file.arrayBuffer()) });
			}
			const blob = new Blob([zipStore(files) as Uint8Array<ArrayBuffer>], { type: 'application/zip' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `${stem}-slices.zip`;
			link.click();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Download failed' });
		} finally {
			busy = false;
		}
	}

	async function renderFull(): Promise<Rendered> {
		if (!sourceSize || !sourceUrl) throw new Error('Open a picture first');
		renderer.setSource({ url: sourceUrl, size: sourceSize, ref: resolvedRef });
		return renderer.render(effectiveSteps(stack), { maxSide: FULL_SIDE });
	}

	async function sendTo(targetId: string) {
		const target = sendTargets.find((entry) => entry.id === targetId);
		if (!target) return;
		await landThenSend(
			prepareSave,
			() => deliverTo(target),
			() => toasts.show('error', notice ?? 'The marked area could not be saved.', { title: 'Send failed' })
		);
	}

	async function deliverTo(target: (typeof sendTargets)[number]) {
		busy = true;
		try {
			const rendered = await renderFull();
			const file = await canvasToPngFile(rendered.canvas, `${stem}-edit.png`);
			const blocked = blockedUploadMessage([file]);
			if (blocked) throw new Error(blocked);
			const stored = await api.uploadMedia(file, 'derived_artifact');
			if (!stored.success || !stored.data) throw new Error(stored.message || 'The picture could not be sent');
			const upload = stored.data;
			const info: UploadFileInfo = {
				id: upload.filename,
				filename: upload.filename,
				original_filename: `${stem}-edit.png`,
				media_type: 'image',
				url: upload.url,
				width: upload.width ?? rendered.size.width,
				height: upload.height ?? rendered.size.height,
				size: upload.size
			};
			const item = pickFromUpload(info).item;
			if (upload.relative_path) {
				item.path = upload.relative_path;
				item.relative_path = upload.relative_path;
			}
			const tab = get(tabsStore).tabs.find((candidate): candidate is GenerationTab => candidate.id === target.tabId && isGenerationTab(candidate));
			if (!tab) throw new Error('That generation tab is closed');
			tabsStore.updateTab(tab.id, {
				formData: { ...tab.formData, ...sendPatch(target.field.name, item, liveMask(mask, effectiveSteps(stack)), target.field.allowInpaint) },
				formReloadGeneration: (tab.formReloadGeneration ?? 0) + 1
			});
			untrack(flushWrite);
			tabsStore.setActiveTab(tab.id);
			toasts.show('success', `${target.field.title} in ${tab.name}`, { title: 'Sent to a generation' });
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Send failed' });
		} finally {
			busy = false;
		}
	}

	async function download() {
		if (!(await prepareSave())) return;
		try {
			const rendered = await renderFull();
			const file = await canvasToPngFile(rendered.canvas, `${stem}-edit.png`);
			const url = URL.createObjectURL(file);
			const link = document.createElement('a');
			link.href = url;
			link.download = file.name;
			link.click();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Download failed' });
		}
	}

	async function copy() {
		if (!(await prepareSave())) return;
		try {
			const rendered = await renderFull();
			const file = await canvasToPngFile(rendered.canvas, `${stem}-edit.png`);
			await navigator.clipboard.write([new ClipboardItem({ 'image/png': file })]);
			toasts.show('success', 'The picture is on your clipboard.', { title: 'Copied' });
		} catch {
			toasts.show('error', 'This browser did not allow copying the picture.', { title: 'Copy failed' });
		}
	}

	function onSave(action: SaveAction) {
		saveMenuOpen = false;
		if (referencesOnly && (action.kind === 'send' || action.kind === 'new-generation')) {
			const target = action.kind === 'send' ? referenceTargets.find((entry) => entry.id === action.target) : null;
			referencesOnly = false;
			if (action.kind === 'new-generation') newReferencesTab(sliceItems);
			else if (target) commitReferences(target, sliceItems);
			return;
		}
		if (saveSlice) {
			if (action.kind === 'save-new' || action.kind === 'save') void runSliceSave();
			else if (action.kind === 'save-sheet') void runSave('new');
			else if (action.kind === 'send') void runSliceSave(referenceTargets.find((entry) => entry.id === action.target) ?? null);
			else if (action.kind === 'download') void downloadSlices();
			else if (action.kind === 'new-generation') void runSliceSave('new-tab');
			return;
		}
		if (action.kind === 'save-new') void runSave('new');
		else if (action.kind === 'save') void runSave(saved ? 'update' : 'new');
		else if (action.kind === 'replace') {
			if (replace.allowed) void runSave('replace');
		} else if (action.kind === 'send') void sendTo(action.target);
		else if (action.kind === 'download') void download();
		else if (action.kind === 'copy') void copy();
	}

	function primarySave() {
		if (!canSave) return;
		if (saveSlice) void runSliceSave();
		else void runSave(saved ? 'update' : 'new');
	}

	function openSource(next: MediaRef) {
		emptyError = null;
		host.setSource(next);
		if (!activeTool) {
			activeTool = 'crop';
			host.setActiveTool('crop');
		}
	}

	async function openFile(file: File) {
		if (!file.type.startsWith('image/')) {
			emptyError = 'That file is not a picture.';
			return;
		}
		emptyBusy = true;
		emptyError = null;
		try {
			const artifact = await uploadMediaToolArtifact(file);
			openSource(artifactRef(artifact, file.name));
		} catch (error) {
			emptyError = describeMediaToolFailure(error);
		} finally {
			emptyBusy = false;
		}
	}

	let replacePicker = $state<ReturnType<typeof ImageSourcePicker>>();
	let replaceBlocked = $derived(busy || !!saving);

	async function changeSource(next: MediaRef) {
		if (!source) {
			openSource(next);
			return;
		}
		if (replaceBlocked) return;
		untrack(flushWrite);
		await replaceSource(host, source, next, dirtyCount, 'picture');
	}

	async function changeFile(file: File) {
		if (!file.type.startsWith('image/')) {
			toasts.show('error', 'That file is not a picture.', { title: 'Replace failed' });
			return;
		}
		busy = true;
		try {
			const artifact = await uploadMediaToolArtifact(file);
			busy = false;
			await changeSource(artifactRef(artifact, file.name));
		} catch (error) {
			toasts.show('error', describeMediaToolFailure(error), { title: 'Replace failed' });
		} finally {
			busy = false;
		}
	}

	function onPaste(event: ClipboardEvent) {
		const file = pastedImage(event.clipboardData?.items);
		if (!file) return;
		if (!source) {
			event.preventDefault();
			void openFile(file);
			return;
		}
		if (isEditableTarget(event.target) || paintActive || replaceBlocked) return;
		event.preventDefault();
		void changeFile(file);
	}

	let optionsTitle = $derived(activeTool ? toolMeta(activeTool, catalog).label : 'Tools');
	let optionsIcon = $derived(activeTool ? toolMeta(activeTool, catalog).icon : 'sliders');
	let applyLabel = $derived(
		activeTool === 'crop'
			? 'Apply crop'
			: activeTool === 'slice'
				? 'Apply slice'
				: activeTool === 'mask'
					? 'Save mask'
					: paintActive
						? 'Apply paint'
						: looksOrCard
							? effectParams?.kind === 'effect'
								? effectUpdating
									? 'Update effect'
									: 'Add effect'
								: 'Apply look'
							: registeredTool && applyOverride?.tool === activeTool
								? applyOverride.label
								: 'Apply'
	);
	let canApply = $derived(
		!busy &&
			(mode === 'paint'
				? !!paintState?.ready
				: mode === 'mask'
					? maskDirty
					: activeTool === 'slice'
						? pendingHere?.tool === 'slice' && !sliceLocked
						: looksOrCard
							? !!pendingHere && canApplyEffect(effectParams, effectOriginal, effectUpdating)
							: registeredTool && applyOverride?.tool === activeTool
								? applyOverride.enabled
								: !!pendingHere || !!catalogTool)
	);
	let canReset = $derived(
		!busy &&
			(liveEntry
				? appliedSet.has(activeTool!)
				: looksOrCard
					? canResetEffect(effectParams, $filterCatalog.items, stillEffects)
					: !!pendingHere || mode === 'mask' || mode === 'paint')
	);
	let phoneBarred = $derived(phone && activeTool === 'effects');
	let pendingTools = $derived([
		...(pending ? [pending.tool === 'effects' && effectCard && activeTool ? activeTool : pending.tool === 'paint' ? paintEntryId('brush') : pending.tool] : []),
		...(mode === 'paint' && paintState?.dirty && activeTool ? [activeTool] : []),
		...(maskDirty ? ['mask'] : [])
	]);
	let originalSummary = $derived(sourceSize ? `${sourceSize.width}×${sourceSize.height} · ${extension}` : extension);
	let stackMore = $derived(applied.length ? [{ id: 'clear', label: 'Remove all edits', icon: 'trash' }] : []);

	let currentStep = $derived(applied.length && !stack.pending ? applied[applied.length - 1] : null);
	let sliceSaveMode = $derived(!!saveSlice || referencesOnly);
	let statusInfo = $derived(
		statusParts({
			tool: activeTool,
			areaTool: taskTool || activeTool === 'mask',
			size: outputDims,
			marked: $maskArea,
			output:
				activeTool === 'crop' && (cropInput ?? cropFrame)
					? planOutputSize(cropParams.plan, (cropInput ?? cropFrame)!)
					: activeTool === 'resize' && resizeParams
						? { width: resizeParams.width, height: resizeParams.height }
						: null
		})
	);
	let footerActions = $derived(phone ? !phoneBarred && !taskTool : !taskTool && !!activeTool);

	const menuCommands: EditorMenuCommands = {
		save: (action) => onSave(action),
		closeEditor: () => requestCloseTab(host.tabId),
		replaceWith: (id) => replacePicker?.open(id as SourceEntryId),
		openRecent: (id) => void openRecentEdit(id),
		openAllEdits: () => openAllEdits(),
		undo: () => doUndo(),
		redo: () => doRedo(),
		apply: () => void applyTool(),
		reset: () => resetTool(),
		toggleStepHidden: () => {
			const step = currentStep;
			if (step) stack = toggleHidden(stack, step.id);
		},
		removeStep: () => {
			const step = currentStep;
			if (!step) return;
			generate.stepRemoved(step.id, stack);
			stack = remove(stack, step.id);
		},
		removeAll: () => void removeAllEdits(),
		toggleCompare: () => (compare = !compare),
		zoomIn: () => canvasApi?.zoomIn(),
		zoomOut: () => canvasApi?.zoomOut(),
		fit: () => canvasApi?.showFit(),
		actualSize: () => canvasApi?.showActualSize(),
		toggleEdits: () => (editsHidden = !editsHidden),
		selectTool: (id) => void selectTool(id)
	};

	let menuCtx = $derived<EditorMenuContext>({
		editor: 'image',
		hasSource: !!source,
		phone,
		busy,
		tool: activeTool,
		rail: railGroups,
		groupedTools: true,
		appliedTools: appliedList,
		saved: !!saved && !saveSlice,
		canSave,
		replace: sliceSaveMode ? { allowed: false, reason: 'Slicing never overwrites the sheet' } : replace,
		sendTargets: sliceSaveMode ? referenceTargets : sendTargets,
		newGeneration: sliceSaveMode,
		newGenerationHint: sliceSaveMode ? 'Pick a preset with a references field' : undefined,
		downloadFormat: sliceSaveMode ? 'ZIP' : 'PNG',
		copyLabel: 'Copy image',
		copyReason: sliceSaveMode ? 'Slices are saved or downloaded, not copied' : undefined,
		saveNewLabel: saveSlice ? (sliceSaved ? 'Saved' : `Save ${sliceCount} ${sliceCount === 1 ? 'image' : 'images'}`) : undefined,
		sources: sourceEntries('image', phone).map((entry) => ({ id: entry.id, label: entry.label, icon: entry.icon })),
		currentSource: currentSourceOf(source),
		replaceLabel: 'Replace picture',
		replaceBlocked,
		recentEdits: $menuRecents,
		canUndo: canUndoNow,
		undoLabel: undoText,
		canRedo: canRedoNow,
		redoLabel: redoText,
		apply: activeTool && !taskTool && !liveEntry ? { label: applyLabel, enabled: canApply, reason: 'Nothing to apply yet' } : null,
		reset: !activeTool
			? null
			: taskTool
				? { label: 'Clear area', icon: 'trash', enabled: !!$maskArea, reason: 'Mark an area first' }
				: { label: 'Reset', icon: 'rotate', enabled: canReset, reason: 'Nothing to reset yet' },
		step: currentStep ? { id: currentStep.id, label: currentStep.label, hidden: currentStep.hidden } : null,
		editCount,
		compare,
		canCompare: canCompareNow,
		zoom: canvasApi ? canvasApi.zoomState() : null,
		editsShown: !editsHidden,
		commands: menuCommands
	});

	function onMenuOpened(id: string) {
		if (id === 'file') void menuRecents.load();
	}
</script>

<svelte:window
	onpaste={onPaste}
	onkeydowncapture={onPaintKey}
	onpagehide={() => {
		if (!pendingWrite) return;
		flushWrite();
		tabsStore.persistNow();
	}}
/>

{#snippet optionsBody()}
	{#if notice}
		<p class="text-sm text-danger" role="alert">{notice}</p>
	{/if}
	{#if registeredTool?.options}
		{#if registeredTool.task}
			<MaskOptions bind:brush={maskBrush} bind:erase={maskErase} onclear={resetTool} what={registeredTool.task === 'remove_object' ? 'object you want removed' : null} />
			{#if staleMask}
				<p class="text-sm text-warning" data-mask-stale>The marked area was drawn before a shape change, so it no longer lines up. Mark it again.</p>
			{/if}
		{/if}
		<EditorEntryComponent entry={entryKey('image', registeredTool.id)} componentRef={registeredTool.options} props={{ host: toolHost, phone, tool: registeredTool.id }} onclose={closeActiveTool} />
	{:else if activeTool === 'crop' && cropInput}
		<CropOptions params={cropParams} input={cropInput} {phone} onchange={(next) => setDraft('crop', next)} />
	{:else if activeTool === 'resize' && resizeParams && resizeInput}
		<ResizeOptions params={resizeParams} input={resizeInput} {phone} onchange={(next) => setDraft('resize', next)} />
	{:else if activeTool === 'effects'}
		{#if !phone}
			<input
				type="search"
				class="h-8 w-full rounded border border-line-strong bg-surface-2 px-2.5 text-base text-fg placeholder:text-fg-subtle focus:border-signal/60 focus:outline-none"
				placeholder="Search looks"
				aria-label="Search looks"
				bind:value={query}
				data-looks-search
			/>
		{/if}
		{@render lookControls()}
		{#if lookContains.length && !phone}
			<div class="flex flex-col gap-1.5" data-look-contains-block>
				<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{lookParams?.name} contains</p>
				<div class="flex flex-wrap gap-1.5" data-look-contains>
					{#each lookContains as label (label)}
						<span class="inline-flex h-6 items-center rounded bg-surface-3 px-2 font-mono text-sm text-fg-muted">{label}</span>
					{/each}
				</div>
			</div>
		{/if}
		{#if !phone}
			<LookList items={trayItems} original={{ selected: !lookParams }} thumb={trayThumb} onpick={pickLook} onpreview={(id) => (hoverLook = id)} />
		{/if}
		{#if bakeIsCapped(outputDims) && !phone}
			<p class="text-sm text-warning" data-bake-cap>Looks on pictures over {FULL_SIDE} px are drawn at {FULL_SIDE} px on the long side and scaled back up when saved.</p>
		{/if}
	{:else if paintActive}
		{#if paintSession && paintState}
			<PaintOptions session={paintSession} state={paintState} {phone} layers={false} />
		{:else}
			<div class="flex justify-center py-4"><Spinner /></div>
		{/if}
	{:else if activeTool === 'slice'}
		<SliceOptions
			params={sliceParams}
			names={sliceNameRows}
			{collections}
			fill={sliceFill}
			transparent={!!sliceAnalysis?.transparent}
			{phone}
			locked={sliceLocked}
			adding={sliceAdding}
			selected={sliceSelected}
			selectedNumber={sliceSelected[0] ? (sliceNumbers.get(sliceSelected[0]) ?? null) : null}
			overlap={maxOverlap(sliceParams.cells) > 0.5}
			bind:tab={sliceTab}
			onpreset={setGrid}
			ondetect={runDetect}
			onsnap={(snap) => setSlice({ ...sliceParams, layout: { ...sliceParams.layout, snap } }, snap ? 'snap on' : 'snap off')}
			onoutput={(patch) => setSlice({ ...sliceParams, output: { ...sliceParams.output, ...patch } }, 'output')}
			onpattern={(pattern) => setSlice({ ...sliceParams, pattern }, 'pattern')}
			onrename={(id, name) => setCells(renameCell(sliceParams.cells, id, name === sliceNames.get(id) ? '' : name), 'rename')}
			onskip={(id) => setCells(toggleSkip(sliceParams.cells, id), 'skip')}
			oncollection={(collection) => setSlice({ ...sliceParams, collection }, 'collection')}
			onadd={() => (sliceAdding = !sliceAdding)}
			onclear={() => {
				sliceSelected = [];
				setSlice({ ...sliceParams, cells: [], layout: { ...sliceParams.layout, preset: null, detected: null } }, 'clear');
			}}
			oncell={sliceCellAction}
		/>
		{#if !sliceParams.cells.length && !phone}
			<div class="flex items-start gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" data-slice-intro>
				<Icon name="info" className="mt-px h-3.5 w-3.5 flex-none" />
				<span>Each cell becomes its own Library image. Lines snap to the empty gutters between panels.</span>
			</div>
		{/if}
	{:else if catalogTool || (activeTool && !isCoreTool(activeTool))}
		<CatalogToolOptions tool={catalogTool} values={catalogValues} note={activeTool === 'subject' ? 'Needs a picture with transparency, for example after Color key.' : null} onchange={(next) => setDraft(activeTool!, { values: next })} />
	{:else if !activeTool}
		<p class="text-sm text-fg-muted">Pick a tool on the left.</p>
	{:else}
		<div class="flex justify-center py-4"><Spinner /></div>
	{/if}
{/snippet}

{#snippet lookControls()}
	{#if lookParams}
		<div class="flex flex-col gap-2" data-look-controls>
			<span class="inline-flex h-6 w-fit flex-none items-center rounded bg-signal/10 px-2 font-mono text-sm text-signal" data-bar-look>{lookParams.name}</span>
			<PropertySlider label="Intensity" value={lookParams.intensity} min={0} max={100} defaultValue={trayById.get(lookParams.filter)?.intensity ?? 100} onchange={(value) => setDraft('effects', { ...lookParams, intensity: value })} />
		</div>
	{:else if effectParams?.kind === 'effect'}
		<span class="inline-flex h-6 w-fit flex-none items-center rounded bg-signal/10 px-2 font-mono text-sm text-signal" data-bar-look>{effectParams.name}</span>
	{:else}
		<p class="text-sm text-fg-subtle">Hover a look to preview it, click to choose</p>
	{/if}
{/snippet}

{#snippet saveStateContent()}
	<EditSaveIndicator status={autosaveStatus} />
{/snippet}

{#snippet phoneBar()}
	{#if activeTool === 'effects'}
		{#if lookParams}
			<span class="inline-flex h-6 flex-none items-center rounded bg-signal/10 px-2 font-mono text-sm text-signal" data-bar-look>{lookParams.name}</span>
			<div class="flex min-w-0 flex-1 items-center">
				<PropertySlider inline label="Intensity" value={lookParams.intensity} min={0} max={100} defaultValue={trayById.get(lookParams.filter)?.intensity ?? 100} onchange={(value) => setDraft('effects', { ...lookParams, intensity: value })} />
			</div>
		{:else}
			<span class="min-w-0 flex-1 truncate text-sm text-fg-subtle">{effectParams?.kind === 'effect' ? effectParams.name : 'Tap a look to preview it'}</span>
		{/if}
		<Button variant="secondary" size="sm" class="h-8 flex-none" disabled={!canReset} onclick={resetTool} attrs={{ 'data-options-reset': '' }}>Reset</Button>
		<Button variant="primary" size="sm" class="h-8 flex-none" disabled={!canApply} onclick={() => void applyTool()} attrs={{ 'data-options-apply': '' }}>Apply</Button>
	{/if}
{/snippet}

{#snippet trayThumb(item: { id: string })}
	{#if item.id === '__original'}
		<canvas use:drawSource={thumbSource} class="alpha-checker block h-full w-full object-cover" aria-hidden="true"></canvas>
	{:else}
		{@const look = trayById.get(item.id)}
		{#if look}
			<canvas use:thumbPainter.draw={{ item: look, source: thumbSource }} class="alpha-checker block h-full w-full object-cover" aria-hidden="true"></canvas>
		{/if}
	{/if}
{/snippet}

{#snippet canvasArea()}
	{#if hoverItem}
		<div class="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2" data-preview-chip>
			<CanvasChip caps={false}>
				<Icon name="editor-eye" className="mr-1.5 h-3.5 w-3.5" />Previewing<strong class="mx-1.5 font-semibold">{hoverItem.name}</strong><span class="text-scrim-on/70">click to apply</span>
			</CanvasChip>
		</div>
	{/if}
	{#if suggestedPanels}
		<div class="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-lg border border-scrim-on/10 bg-scrim/70 p-[3px] text-scrim-on backdrop-blur" data-slice-suggest>
			<Button variant="overlay" size="xs" class="h-7" icon="editor-slice" onclick={() => void acceptSliceHint()}>Slice {suggestedPanels} panels</Button>
			<IconButton icon="close" label="Dismiss" size="xs" variant="overlay" class="h-7 w-7" onclick={dismissSliceHint} />
		</div>
	{/if}
	{#if sourceError}
		<div class="flex flex-1 items-center justify-center bg-scrim p-6 text-sm text-danger" role="alert">{sourceError}</div>
	{:else if mode === 'paint'}
		{#if paintBase}
			{#key paintBase}
			{#key `${paintKey}:${pending?.target ?? ''}`}
				<PaintStage
					bind:this={paintStage}
					base={paintBase.canvas}
					layerUrl={pendingHere?.tool === 'paint' ? (pendingHere.params as PaintParams).url : null}
					tool={paintToolOfEntry(activeTool!)}
					name={fileName ?? 'picture'}
					onstate={(session, snapshot) => {
						paintSession = session;
						paintState = snapshot;
					}}
				/>
			{/key}
			{/key}
		{:else}
			<div class="flex flex-1 items-center justify-center bg-scrim"><Spinner /></div>
		{/if}
	{:else if shownSize && (mainRender || cropCanvas)}
		<EditorCanvas bind:this={canvasApi} mediaWidth={shownSize.width} mediaHeight={shownSize.height} bind:viewport {compare} {phone} checker statusSize={statusInfo.size} statusExtra={statusInfo.extra}>
			{#snippet layer()}
				<canvas use:showCanvas={{ canvas: mode === 'crop' ? cropCanvas : (mainRender?.canvas ?? null), onrestore: recoverSurfaces }} class="block h-full w-full" data-editor-layer aria-label="Edited picture"></canvas>
			{/snippet}
			{#snippet before()}
				{#if beforeRender}
					<canvas use:showCanvas={{ canvas: beforeRender.canvas, onrestore: recoverSurfaces }} class="block h-full w-full" data-editor-before aria-hidden="true"></canvas>
				{/if}
			{/snippet}
			{#snippet overlay()}
				{#if mode === 'crop' && cropFrame}
					<CropOverlay
						rect={cropParams.plan.rect}
						ratio={IMAGE_ASPECTS.find((aspect) => aspect.key === cropParams.aspect)?.ratio ?? null}
						stageAspect={cropFrame.width / Math.max(1, cropFrame.height)}
						label="{planOutputSize(cropParams.plan, cropInput ?? cropFrame).width} × {planOutputSize(cropParams.plan, cropInput ?? cropFrame).height} · {aspectLabel(cropParams.aspect)}"
						onchange={(rect) => setDraft('crop', { ...cropParams, plan: { ...cropParams.plan, rect } })}
					/>
				{:else if activeTool === 'slice' && mainRender}
					<SliceOverlay
						cells={sliceParams.cells}
						an={sliceAnalysis}
						size={sliceLogical}
						selected={sliceSelected}
						snap={sliceParams.layout.snap}
						{phone}
						locked={sliceLocked}
						adding={sliceAdding}
						padding={slicePadding}
						states={sliceStates}
						loupeSrc={phone ? sourceUrl : null}
						onchange={(cells, label) => setCells(cells, label)}
						onselect={(ids) => (sliceSelected = ids)}
						onadded={() => (sliceAdding = false)}
					/>
				{:else if mode === 'mask' && mainRender}
					{#key `${mask?.path ?? ''}:${mainRender.canvas.width}x${mainRender.canvas.height}`}
						<MaskLayer
							bind:this={maskLayer}
							width={mainRender.canvas.width}
							height={mainRender.canvas.height}
							existing={liveMaskNow?.url ?? null}
							brush={maskBrush}
							erase={maskErase}
							{phone}
							onstroke={(painted) => {
								if (painted) maskDirty = true;
							}}
							onhistory={(next) => (maskHistory = next)}
						/>
					{/key}
				{/if}
				{#if taskTool && runOutline}
					<StaleOutline rect={runOutline} />
				{/if}
				{#if taskTool && runStep && runStatus && runStatus !== 'done' && runStatus !== 'failed' && runStatus !== 'cancelled'}
					<RunOverlay rect={runView?.run?.rect ?? runStep.params.rect} preview={runView?.preview ?? null} waiting={runStatus === 'queued'} />
				{/if}
			{/snippet}
			{#snippet hint()}
				{#if activeTool !== 'slice'}
					<CanvasChip caps={false}>Space</CanvasChip>drag to pan
				{:else if !sliceParams.cells.length}
					Detect panels or pick a grid
				{:else if sliceAdding}
					Drag on the picture to add a box
				{:else}
					<CanvasChip caps={false}>Alt</CanvasChip>drag places freely<CanvasChip caps={false}>Space</CanvasChip>pans
				{/if}
			{/snippet}
			{#snippet badge()}
				{#if rendering && !phone && activeTool !== 'slice'}
					<CanvasChip><span class="inline-flex items-center gap-1.5"><Spinner size="sm" />Preview</span></CanvasChip>
				{/if}
			{/snippet}
		</EditorCanvas>
		{#if taskTool && runningId && runStatus}
			<div class="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2" data-run-chip-host>
				<RunChip status={runStatus === 'queued' ? 'queued' : 'running'} progress={runView?.progress ?? 0} onclick={() => void generate.cancel(runningId!)} />
			</div>
		{/if}
		{#if taskTool && !phone}
			<div class="pointer-events-none absolute inset-x-0 bottom-16 z-20 flex flex-col items-center gap-2 px-4" data-generate-dock>
				<FloatingPrompt running={runningId} large={largeArea} settings={promptSettings} onclose={closeActiveTool} />
				{#if runStep && runStatus === 'done' && runStep.params.results.length && !runOutOfDate}
					<CandidateStrip results={runStep.params.results} chosen={runStep.params.chosen} onchoose={(index) => generate.choose(runStep!.step.id, index)} onagain={() => void generate.tryAgain(runStep!.step.id)} />
				{/if}
			</div>
		{/if}
		{#if renderError}
			<div class="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
				<span class="rounded bg-danger-solid/90 px-3 py-1 text-sm text-white" role="alert" data-render-error>{renderError}</span>
			</div>
		{/if}
	{:else}
		<div class="flex flex-1 items-center justify-center bg-scrim">
			{#if renderError}
				<span class="rounded bg-danger-solid/90 px-3 py-1 text-sm text-white" role="alert" data-render-error>{renderError}</span>
			{:else}
				<Spinner />
			{/if}
		</div>
	{/if}
{/snippet}

{#snippet stackView(variant: 'panel' | 'sheet')}
	<EditStack
		{stack}
		original={{ summary: originalSummary, thumb: sourceUrl }}
		{variant}
		folded={settingsOpen && !phone}
		bind:collapsed={editsHidden}
		pinned={IMAGE_PINNED_LANES}
		moreItems={stackMore}
		rowInfo={rowInfoFor}
		progress={stackGroup}
		onrowaction={(id, action) => {
			const step = stack.steps.find((candidate) => candidate.id === id);
			if (!step) return;
			if (step.tool === 'generate') {
				if (action === 'cancel') void generate.cancel(id);
				else if (action === 'again') void generate.tryAgain(id);
				else if (action === 'retry') void generate.retry(id);
				else if (action === 'redo') void redoStep(id);
				return;
			}
			runStepAction(step, action);
		}}
		onreopen={(id) => void reopenStep(id)}
		onseek={(cursor) => (stack = seek(stack, cursor))}
		ontogglehidden={(id) => (stack = toggleHidden(stack, id))}
		onremove={(id) => {
			generate.stepRemoved(id, stack);
			stack = remove(stack, id);
		}}
		onmove={(id, to) => {
			if (canMoveWithSlice(stack, id, to)) stack = move(stack, id, to, IMAGE_PINNED_LANES);
		}}
		onundo={doUndo}
		onredo={doRedo}
		onmore={(id) => {
			if (id === 'clear') void removeAllEdits();
		}}
	>
		{#snippet progressRow(index)}
			{#if registeredTool?.children}
				<EditorEntryComponent entry={entryKey('image', registeredTool.id)} componentRef={registeredTool.children} props={{ host: toolHost, phone, index, session: paintSession, state: paintState }} />
			{/if}
		{/snippet}
		{#snippet notes()}
			{#if (sliceInStack || appliedSlice) && !phone}
				<div class="mx-2 mb-2.5 mt-1 flex flex-none items-start gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" data-stack-note="slice">
					<Icon name="info" className="mt-px h-3.5 w-3.5 flex-none" />
					<span>Slice is always last. Edits you add go above it and apply to every cell.</span>
				</div>
			{/if}
		{/snippet}
	</EditStack>
{/snippet}

{#snippet sliceRows(pick: (action: SaveAction) => void)}
	<SliceSaveRows count={sliceCount || sliceItems.length} destination={sliceDestination} targets={referenceTargets} {referencesOnly} canSaveSheet={withoutSlice(effective).length > 0} onchoose={pick} />
{/snippet}

{#snippet saveRows(pick: (action: SaveAction) => void)}
	<SaveMenuRows saved={!!saved} {replace} {sendTargets} downloadFormat="PNG" copyLabel="Copy image" notices={saveNotices} onchoose={pick} />
{/snippet}

{#snippet replaceFooter(close: () => void)}
	<RecentPictures
		columns="grid-cols-3"
		onpick={(ref) => {
			close();
			void changeSource(ref);
		}}
	/>
	{#if !phone}
		<p class="mt-2 flex items-center gap-2 text-sm text-fg-subtle">or paste <Kbd keys="Ctrl V" /></p>
	{/if}
{/snippet}

{#snippet replaceMenu()}
	<ReplaceMenu label="Replace picture" entries={sourceEntries('image', phone)} current={currentSourceOf(source) as SourceEntryId | null} disabled={replaceBlocked} {phone} onselect={(id) => replacePicker?.open(id)}>
		{#snippet footer(close)}
			{@render replaceFooter(close)}
		{/snippet}
	</ReplaceMenu>
{/snippet}

{#snippet menuChildFooter(slot: { id: string; close: () => void })}
	{#if slot.id === 'file.replace'}
		<div class="mt-1 border-t border-line px-2.5 pb-1 pt-2">{@render replaceFooter(slot.close)}</div>
	{/if}
{/snippet}

<ImageSourcePicker bind:this={replacePicker} onpick={(ref) => void changeSource(ref)} onfile={(file) => void changeFile(file)} />

<div class="image-editor-tab flex h-full min-h-0 w-full flex-col" data-image-editor data-tool={activeTool ?? ''} data-mode={mode}>
	{#if autosaveStatus === 'conflict' || autosaveStatus === 'too-large'}
		<div class="flex-none" data-edit-notice-host>
			<EditConflictNotice kind={autosaveStatus} busy={autosaveBusy} onload={() => void loadNewerEdit()} onkeep={() => void keepMineAsCopy()} />
		</div>
	{/if}
	{#if !source}
		<ImageStartScreen tabId={host.tabId} {phone} busy={emptyBusy} error={emptyError} onpick={openSource} onfile={(file) => void openFile(file)} />
	{:else}
		<EditorFrame
			{phone}
			bind:compare
			bind:stackOpen
			canCompare={canCompareNow}
			barVisible={phone && activeTool === 'effects'}
			{editCount}
			phoneSize={outputDims ? `${outputDims.width}×${outputDims.height}` : null}
			onundo={doUndo}
			onredo={doRedo}
			onsave={primarySave}
			onapply={() => void enterKey()}
			stack={stackView}
		>
			{#snippet phoneLead()}
				{@render replaceMenu()}
				<EditSaveIndicator status={autosaveStatus} chip />
			{/snippet}
			{#snippet toolbar()}
				{#if phone}
					{#if phoneMenuOpen}
						<EditorMenuSheet ctx={menuCtx} {fileName} onclose={() => (phoneMenuOpen = false)} onopenmenu={onMenuOpened} />
					{/if}
					{#if saveMenuOpen}
						<FrameSheet label="Save" onclose={() => (saveMenuOpen = false)}>
							{#snippet header()}
								<span class="text-lg font-semibold text-fg">Save</span>
								<span class="flex-1"></span>
								<span class="truncate font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{fileName}</span>
							{/snippet}
							<div role="menu" aria-label="Save" class="flex flex-col">
								{#if saveSlice || referencesOnly}
									{@render sliceRows(onSave)}
								{:else}
									{@render saveRows(onSave)}
								{/if}
							</div>
						</FrameSheet>
					{/if}
				{:else}
					<EditorToolbar
						{fileName}
						title={activeTool ? optionsTitle : 'Image editor'}
						titleIcon={optionsIcon}
						menu={menuCtx}
						onopenmenu={onMenuOpened}
						childFooter={menuChildFooter}
						canUndo={canUndoNow}
						canRedo={canRedoNow}
						undoLabel={undoText}
						redoLabel={redoText}
						{canSave}
						saved={!!saved}
						{replace}
						{sendTargets}
						saveNotices={saveNotices}
						downloadFormat="PNG"
						{saving}
						saveLabel={saveSlice ? (sliceSaved ? 'Saved' : `Save ${sliceCount} ${sliceCount === 1 ? 'image' : 'images'}`) : null}
						menuRows={saveSlice || referencesOnly ? sliceRows : undefined}
						bind:saveMenuOpen
						onundo={doUndo}
						onredo={doRedo}
						onsave={onSave}
						oncancelsave={cancelSave}
						saveState={autosaveStatus === 'idle' ? undefined : saveStateContent}
					/>
				{/if}
			{/snippet}
			{#snippet rail()}
				<ToolRail groups={railGroups} active={activeTool} pending={pendingTools} applied={appliedList} memoryKey="image" groupedStrip variant={phone ? 'strip' : 'icons'} onselect={(id) => void selectTool(id)} />
			{/snippet}
			{#snippet canvas()}
				{@render canvasArea()}
			{/snippet}
			{#snippet bar()}
				{#if phoneBarred}
					{@render phoneBar()}
				{/if}
			{/snippet}
			{#snippet below()}
				{#if taskTool && phone}
					{#if runStep && runStatus === 'done' && runStep.params.results.length && !runOutOfDate}
						<div class="flex flex-none justify-center bg-canvas pb-2" data-generate-strip-phone>
							<CandidateStrip phone results={runStep.params.results} chosen={runStep.params.chosen} onchoose={(index) => generate.choose(runStep!.step.id, index)} onagain={() => void generate.tryAgain(runStep!.step.id)} />
						</div>
					{/if}
					{#if settingsOpen && settingsRef}
						<EditorEntryComponent entry={entryKey('image', registeredTool!.id)} componentRef={settingsRef} props={{ host: toolHost, phone, onback: () => (settingsFor = null) }} onclose={() => (settingsFor = null)} />
					{:else}
						<div class="flex-none border-t border-line bg-surface-1 px-3 py-2" data-task-mark-bar>
							<MaskOptions compact bind:brush={maskBrush} bind:erase={maskErase} onclear={resetTool} />
						</div>
						<FloatingPrompt phone running={runningId} large={largeArea} settings={promptSettings} onclose={closeActiveTool} />
					{/if}
				{:else if phone && activeTool === 'effects'}
					<div class="max-h-[34vh] flex-none overflow-y-auto border-t border-line bg-surface-1 px-3 py-2" data-look-list-phone>
						<LookList items={trayItems} original={{ selected: !lookParams }} thumb={trayThumb} onpick={pickLook} onpreview={(id) => (hoverLook = id)} />
					</div>
				{:else if activeTool === 'slice' && sliceParams.cells.length}
					<SliceStrip
						items={sliceStrip}
						skipped={sliceSkipped}
						{phone}
						locked={sliceLocked}
						destination={sliceDestination}
						onrestore={(id) => setCells(toggleSkip(sliceParams.cells, id), 'keep')}
						onreorder={(id, to) => setCells(reorderKept(sliceParams.cells, id, to), 'reorder')}
					/>
				{/if}
			{/snippet}
			{#snippet options()}
				{#if settingsOpen && settingsRef && !phone}
					<EditorEntryComponent entry={entryKey('image', registeredTool!.id)} componentRef={settingsRef} props={{ host: toolHost, phone, onback: () => (settingsFor = null) }} onclose={() => (settingsFor = null)} />
				{:else if !(phoneBarred && activeTool === 'effects' && !stillEffects.length) && !(phone && taskTool)}
					<OptionsPanel
						title={optionsTitle}
						icon={optionsIcon}
						{applyLabel}
						{canApply}
						{canReset}
							showApply={!liveEntry}
							actions={footerActions}
						{phone}
						onapply={() => void applyTool()}
						onreset={resetTool}
					>
						{@render optionsBody()}
					</OptionsPanel>
				{/if}
			{/snippet}
		</EditorFrame>
	{/if}
</div>
