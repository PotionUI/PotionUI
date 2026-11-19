<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { get } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import { pickFromUpload } from '$lib/components/form-fields/mediaLoaderPicks';
	import { canvasToPngFile } from '$lib/paint/flatten';
	import { PaintSession, type SessionSnapshot } from '$lib/paint/session';
	import type { Command } from '$lib/paint/history';
	import type { Layer } from '$lib/paint/types';
	import { fitInside } from '$lib/paint/geometry';
	import { Button, IconButton, Kbd, Menu, Spinner } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import {
		CanvasChip,
		EditStack,
		EditorCanvas,
		EditorFrame,
		EditorToolbar,
		FrameSheet,
		LookList,
		OptionsPanel,
		ToolRail
	} from '$lib/components/tool-tabs/frame';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import SaveMenuRows from '$lib/components/tool-tabs/frame/SaveMenuRows.svelte';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';
	import { editorsRevision, entryKey } from '$lib/editors/registry';
	import { canvasActionsOf, railTools } from '$lib/editors/railTools';
	import { overlays } from '$lib/editors/overlays';
	import { tasks } from '$lib/editors/tasks';
	import { reopenToolOf, stepKinds } from '$lib/editors/stepKinds';
	import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
	import { editCloseLoss } from '$lib/tool-tabs/closeGuard';
	import { imageRowInfo, outOfDateCount, outOfDateNotice, runStepAction, stepOutOfDate, stepsBeneath } from '$lib/tool-tabs/image/stepKinds';
	import { createTabGenerate } from '$lib/tool-tabs/image/generate/wiring';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { isLargeArea } from '$lib/tool-tabs/image/generate/area';
	import { generateSteps, leftOutCount, leftOutNotice, shapeChange, stepsBeneathOf } from '$lib/tool-tabs/image/generate/generateStep';
	import { runViews } from '$lib/tool-tabs/image/generate/runEngine';
	import { maskWhatOf } from '$lib/tool-tabs/image/generate/taskOptions';
	import { settingsOf } from '$lib/tool-tabs/image/generate/register';
	import { canvasFromUrl } from '$lib/paint/loadPicked';
	import FloatingPrompt from '$lib/components/tool-tabs/generate/FloatingPrompt.svelte';
	import CandidateStrip from '$lib/components/tool-tabs/generate/CandidateStrip.svelte';
	import RunChip from '$lib/components/tool-tabs/generate/RunChip.svelte';
	import DockStaleNotice from '$lib/components/tool-tabs/generate/DockStaleNotice.svelte';
	import RunOverlay from '$lib/components/tool-tabs/generate/RunOverlay.svelte';
	import StaleOutline from '$lib/components/tool-tabs/generate/StaleOutline.svelte';
	import { LASSO_OVERLAY, chooseOverlay, maskArea, maskToolEntries, maskToolState } from '$lib/tool-tabs/image/maskOverlays';
	import { focusDockPrompt, handleEscape, maskControls, type ImageCanvasSurface } from '$lib/tool-tabs/image/canvasActions';
	import type { Point } from '$lib/paint/types';
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
		duplicateStep,
		effectiveSteps,
		insertSteps,
		renameStep,
		isLive,
		mergeInto,
		move,
		opacityOf,
		previewWith,
		redo,
		redoLabel,
		remove,
		reopen,
		seek,
		setOpacity,
		toggleHidden,
		toggleHiddenUndoable,
		undo,
		undoLabel,
		updatePending,
		type EditStackState,
		type EditStep,
		type StepDraft
	} from '$lib/tool-tabs/frame/editStack';
	import type { RailGroup, SaveAction, SavingProgress } from '$lib/tool-tabs/frame/types';
	import { fitViewport } from '$lib/tool-tabs/frame/viewport';
	import { expandUi } from '$lib/tool-tabs/image/generate/expand';
	import { geometryKey, liveMask, maskChannelOf, maskIsStale, sendPatch, type MaskChannel } from '$lib/tool-tabs/image/mask';
	import { SWITCH_TO_LAYERS, aboveKeyOf, aboveLayerOf, arrangeLayers, isFlattenedPaint, isLayerStep, layerOrdered, layerThumb, layersOf, mergeLayerPixels, patchMaskOf, settledLayered, switchToLayers } from '$lib/tool-tabs/image/layers';
	import { PaintLayerStore, blankCanvas, copyCanvas, diffCommand, docCanvas, isNormalized, newLayer, nextPaintName, nextPictureName, normalizeLayer, paintInto, relinkLayer } from '$lib/tool-tabs/image/paintLayers';
	import { confirmDialog } from '$lib/stores/confirm';
	import PatchMaskOverlay from '$lib/components/tool-tabs/image/PatchMaskOverlay.svelte';
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
	import { createMenuRecents, openRecentEdit } from '$lib/tool-tabs/image/menuEdits';
	import { statusParts } from '$lib/tool-tabs/frame/statusLine';
	import { requestCloseTab } from '$lib/tabs/closeConfirm';
	import {
		CATALOG_TOOL,
		LEGACY_TOOL_ALIAS,
		IMAGE_PINNED_LANES,
		adjustDraft,
		catalogDraft,
		cropDraft,
		GEOMETRY_LANE,
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
	import { saveNameFor } from '$lib/tool-tabs/saveName';
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
	import ToolEntryPanel from '$lib/components/tool-tabs/frame/ToolEntryPanel.svelte';
	import CropOptions, { aspectLabel, IMAGE_ASPECTS } from './CropOptions.svelte';
	import CropOverlay from './CropOverlay.svelte';
	import ImageStartScreen from './ImageStartScreen.svelte';
	import ImageSourcePicker from './ImageSourcePicker.svelte';
	import RecentEditsDialog from './RecentEditsDialog.svelte';
	import RecentPictures from './RecentPictures.svelte';
	import ReplaceMenu from '../frame/ReplaceMenu.svelte';
	import { replaceSource, sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import { currentSourceOf } from '$lib/components/form-fields/mediaFieldSources';
	import { isEditableTarget } from '$lib/utils/editableTarget';
	import { pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import MaskLayer from './MaskLayer.svelte';
	import MaskOptions from './MaskOptions.svelte';
	import PaintOptions from './PaintOptions.svelte';
	import ImageSourceMenu from '$lib/components/paint/ImageSourceMenu.svelte';
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
	let sequential = $state(!layerOrdered(untrack(() => stack)));
	let sequentialNoteDismissed = $state(false);
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
	let recentDialog = $state(false);
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
	let paintBaseKey = $state<string | null>(null);
	let renderError = $state<string | null>(null);
	let rendering = $state(false);
	let surfaceVersion = $state(0);

	type HistoryMark = { kind: 'stack' } | { kind: 'pixels'; id: string; command: Command };
	const ORIGINAL_ROW = 'original';
	let paintSession = $state.raw<PaintSession | null>(null);
	let paintState = $state.raw<SessionSnapshot | null>(null);
	let paintRow = $state<string | null>(null);
	let layerRevision = $state(0);
	let undoMarks = $state.raw<HistoryMark[]>([]);
	let redoMarks = $state.raw<HistoryMark[]>([]);
	const layerStore = new PaintLayerStore({
		upload: async (file) => {
			const artifact = await uploadMediaToolArtifact(file);
			return { id: artifact.id, url: artifact.url };
		},
		uploaded: (id, previous, next) => (stack = relinkLayer(stack, id, previous, next)),
		changed: () => (layerRevision += 1),
		failed: (error) => (notice = describeMediaToolFailure(error))
	});
	onDestroy(() => layerStore.dispose());
	let maskLayer = $state<ReturnType<typeof MaskLayer> | undefined>();
	let maskBrush = $state(48);
	let maskErase = $state(false);
	let maskDirty = $state(false);
	let maskSeed = $state.raw<{ path: Point[]; width: number; height: number } | null>(null);
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
		render: async (stepsBeneath) => (await renderer.render(layerStore.display(stepsBeneath === undefined ? effective : effective.slice(0, stepsBeneath)))).canvas,
		setApply: (state) => (applyOverride = state ? { ...state, tool: activeTool } : null),
		writeLive: (next, options) => writeLive(next, options),
		settleLive: () => settleLive()
	});
	$effect(() => toolHost.syncPending(stack.pending));
	$effect(() => toolHost.syncStack(stack));
	let regionCache: { path: string; id: string; url: string } | null = null;
	const generate = createTabGenerate({
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: resolvedRef }),
		editLink: () => editLinkForSave(() => autosave.ensure()),
		areaSize: () => get(maskArea),
		applyMatte: async (url) => (maskLayer ? await maskLayer.importBackground(url) : false),
		landPicture: async (source) => {
			if (!(source instanceof File) && !source.url) return null;
			const file = source instanceof File ? source : await canvasToPngFile(await canvasFromUrl(source.url!), `${stem}-background.png`);
			const artifact = await uploadMediaToolArtifact(file);
			return { item_id: artifact.id, url: artifact.url, name: source instanceof File ? source.name : (source.name ?? null) };
		},
		pictureSize: (id) => {
			const list = runBase(appliedSteps(stack), id);
			const at = id ? list.findIndex((step) => step.id === id) : -1;
			return outputSize(at >= 0 ? list.slice(0, at) : list, sourceSize);
		},
		spawnEdit: async (base, name) => {
			const link = editSourceOf(host.source);
			if (!link) return null;
			try {
				const label = mediaFileStem(host.source);
				const response = await api.createEdit({ tool: 'image', name: (label ? `${label} · ${name}` : name).slice(0, 120), source: link, state: writeImageState({ stack: base, activeTool: null, viewport: fitViewport(), mask: null, saved: null }) });
				const record = response.success && response.data ? response.data.edit : null;
				return record ? { id: record.id, revision: record.revision } : null;
			} catch {
				return null;
			}
		},
		saveSpawned: async (link, next) => {
			try {
				const response = await api.saveEdit(link.id, { revision: link.revision, state: writeImageState({ stack: next, activeTool: null, viewport: fitViewport(), mask: null, saved: null }) });
				return response.success !== false;
			} catch {
				return false;
			}
		},
		beneath: (id) => stepsBeneathOf(runBase(appliedSteps(stack), id), id),
		stepsBeneath: async (id) => {
			settleLayer();
			if (!(await layerStore.flush())) throw new Error('A layer could not be saved');
			let list = runBase(effective, id);
			const at = id ? list.findIndex((step) => step.id === id) : -1;
			if (at >= 0) list = list.slice(0, at);
			return renderer.payload(list);
		},
		landRegion: async (soft) => {
			if (soft && maskLayer?.hasStrokes()) {
				const canvas = maskLayer.renderSoftMask();
				if (canvas) {
					const artifact = await uploadMediaToolArtifact(await canvasToPngFile(canvas, `${stem}-matte.png`));
					return { item_id: artifact.id, url: artifact.url };
				}
			}
			if (!(await landMask())) return null;
			const live = liveMaskNow;
			if (!live) return null;
			if (regionCache?.path === live.path) return { item_id: regionCache.id, url: regionCache.url };
			const file = await canvasToPngFile(await canvasFromUrl(live.url), `${stem}-area.png`);
			const artifact = await uploadMediaToolArtifact(file);
			regionCache = { path: live.path, id: artifact.id, url: artifact.url };
			return { item_id: artifact.id, url: artifact.url };
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
	let selectedStepId = $derived.by(() => {
		if (paintActive) return paintRow;
		if (stack.pending?.target) return stack.pending.target;
		if (!isLiveEntry(activeTool)) return null;
		return applied.findLast((step) => entryOfStep(step) === activeTool)?.id ?? null;
	});
	let editCount = $derived(appliedCount(stack));
	let paintActive = $derived(isPaintEntry(activeTool));
	let toolKey = $derived(paintActive ? 'paint' : activeTool);
	let unsavedLayers = $derived.by(() => {
		void layerRevision;
		return layerStore.unsaved;
	});
	let dirtyCount = $derived(unsavedEdits(stack, saved) + (unsavedLayers ? 1 : 0));
	let replace = $derived(replaceOption(origin));
	let fileName = $derived(source ? (source.name ?? mediaFileStem(source) ?? 'Picture') : null);
	let stem = $derived.by(() => {
		void $tabsStore;
		return saveNameFor(host.tabId, source) ?? 'picture';
	});
	let extension = $derived((fileName?.split('.').pop() ?? 'png').toUpperCase());
	let layerTask = $derived(!sequential && !!registeredTool?.task && !tasks.get('image', registeredTool.task)?.grows);

	function runBase<S extends EditStep>(list: S[], id: string | null): S[] {
		if (sequential) return list;
		const existing = id ? list.find((step) => step.id === id) : null;
		if (existing) return isLayerStep(existing) ? layersOf(list) : list;
		const task = registeredTool?.task ? tasks.get('image', registeredTool.task) : null;
		return task?.grows ? list : layersOf(list);
	}
	let maskBasis = $derived(layerTask ? layersOf(effective) : effective);
	let liveMaskNow = $derived(liveMask(mask, maskBasis));
	let staleMask = $derived(maskIsStale(mask, maskBasis));

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
	let paintStep = $derived(paintActive && paintRow ? (applied.find((step) => step.id === paintRow) ?? null) : null);
	let paintTargetStep = $derived(paintStep?.tool === 'paint' ? paintStep : null);
	let paintTargetId = $derived(paintTargetStep?.id ?? null);
	let stepsBeforeTarget = $derived(targetIndex >= 0 ? effective.slice(0, targetIndex) : effective);

	let growsTool = $derived.by(() => {
		void $editorsRevision;
		return registeredTool?.task ? !!tasks.get('image', registeredTool.task)?.grows : false;
	});
	let mode = $derived<'paint' | 'mask' | 'crop' | 'preview'>(
		paintActive ? 'paint' : activeTool === 'mask' || (registeredTool?.task && !growsTool) ? 'mask' : activeTool === 'crop' ? 'crop' : 'preview'
	);
	let taskTool = $derived(!!registeredTool?.task);
	$effect(() => {
		const size = outputSize(appliedSteps(stack), sourceSize);
		untrack(() =>
			expandUi.update((ui) => (ui.source?.width === size?.width && ui.source?.height === size?.height ? ui : { ...ui, source: size ? { width: size.width, height: size.height } : null }))
		);
	});
	let settingsRef = $derived(settingsOf(registeredTool));
	let dockRef = $derived(registeredTool?.dock ?? (registeredTool?.task ? { component: FloatingPrompt } : undefined));
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
		if (!taskTool || growsTool) return;
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
	let dockHasTemp = $derived(!!runStep && ((runStatus === 'done' && (runStep.params.results.length > 0 || runOutOfDate))));
	let runOutline = $derived(runStep && runOutOfDate && shapeChange(runStep.params, runBeneath) === 'resize' ? runStep.params.rect : null);

	async function redoStep(id: string) {
		const outcome = await generate.redo(id);
		if (outcome.kind === 'area') await selectTool(outcome.task);
	}

	let cropParams = $derived<CropParams>((pendingHere?.tool === 'crop' ? (pendingHere.params as CropParams) : null) ?? { plan: EMPTY_IMAGE_PLAN, aspect: 'free' });
	let catalogTool = $derived(activeTool && !registeredTool?.options && !registeredTool?.schema && !['crop', 'resize', 'paint', 'mask', 'effects', 'adjust', 'slice'].includes(activeTool) && !paintActive ? catalog.find((tool) => tool.id === (CATALOG_TOOL[activeTool!] ?? activeTool)) ?? null : null);
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
	let intensityConfig = $derived({ title: 'Intensity', min: 0, max: 100, step: 1, default: lookParams ? (trayById.get(lookParams.filter)?.intensity ?? 100) : 100, reset: true, legend: false });
	let lookContains = $derived(
			lookParams ? [...new Set((trayById.get(lookParams.filter)?.steps ?? []).map((step) => getOp(step.op, extensions)?.label).filter((label): label is string => !!label))] : []
		);
		let hoverItem = $derived<FilterItem | null>(activeTool === 'effects' && hoverLook && lookParams?.filter !== hoverLook ? (trayById.get(hoverLook) ?? null) : null);
	let hoverDraft = $derived(hoverItem ? (lookDraft({ kind: 'look', filter: hoverItem.id, name: hoverItem.name, intensity: hoverItem.intensity }) as StepDraft<unknown>) : null);
	let canCompareNow = $derived(mode === 'preview' && (liveEntry ? appliedSet.has(activeTool!) : !!pending));
	let showMask = $state(false);
	let shownMask = $derived.by(() => {
		if (!showMask || mode === 'paint' || !selectedStepId) return null;
		const step = applied.find((entry) => entry.id === selectedStepId);
		return step && !step.hidden ? patchMaskOf(step) : null;
	});
	let shownMaskLayers = $derived(!!shownMask && !sequential);
	let mainSteps = $derived(
		mode === 'preview'
			? shownMaskLayers
				? layersOf(effective)
				: previewWith(stack, hoverDraft)
			: mode === 'crop'
				? pendingHere?.tool === 'crop'
					? stepsBeforeTarget
					: effective
				: mode === 'mask'
					? maskBasis
					: effective
	);
	let beforeList = $derived(
		mode === 'preview' && compare ? (liveEntry ? effective.filter((step) => entryOfStep(step) !== activeTool) : pending ? beforeSteps(stack) : []) : null
	);
	let thumbList = $derived(activeTool === 'effects' ? (pending ? beforeSteps(stack) : effective) : null);
	let addSource = $state<'library' | 'history' | 'file' | null>(null);
	let picturePending: { id: string; canvas: HTMLCanvasElement } | null = null;
	let paintSplit = $derived.by(() => {
		if (mode !== 'paint') return null;
		const at = paintStep ? applied.indexOf(paintStep) : -1;
		const cut = paintRow === ORIGINAL_ROW ? 0 : at < 0 ? applied.length : paintTargetStep ? at : at + 1;
		const beneath = applied.slice(0, cut).filter((step) => !step.hidden);
		const rest = applied.slice(paintTargetStep ? at + 1 : cut);
		const stop = rest.findIndex((step) => step.lane === GEOMETRY_LANE);
		const above = (stop >= 0 ? rest.slice(0, stop) : rest).filter((step) => !step.hidden && isLayerStep(step));
		return { below: sequential ? beneath : layersOf(beneath), above };
	});
	let paintBelowDraft = $derived.by(() => {
		void layerRevision;
		return paintSplit ? layerStore.display(paintSplit.below) : null;
	});
	let paintBelowKey = $derived(paintBelowDraft ? stepsKey(paintBelowDraft) : null);
	let paintBelow = $derived.by(() => {
		void paintBelowKey;
		return untrack(() => paintBelowDraft);
	});
	type AboveDraw = { key: string; local: Layer | null; url: string | null; rect: { x: number; y: number; width: number; height: number } | null; opacity: number };
	let paintAboveLayers = $derived.by(() => {
		void layerRevision;
		return (paintSplit?.above ?? []).flatMap((step): AboveDraw[] => {
			const local = step.tool === 'paint' ? layerStore.layer(step.id) : null;
			if (local) return [{ key: `${step.id}@${layerStore.version(step.id)}@${opacityOf(step)}`, local, url: null, rect: null, opacity: opacityOf(step) }];
			const layer = aboveLayerOf(step);
			return layer ? [{ key: aboveKeyOf([layer]), local: null, ...layer }] : [];
		});
	});
	let paintAboveKey = $derived(paintAboveLayers.map((layer) => layer.key).join('|'));
	let addAboveLabel = $derived.by(() => {
		if (selectedStepId === ORIGINAL_ROW) return 'Add above Original';
		const step = selectedStepId ? applied.find((entry) => entry.id === selectedStepId) : null;
		return step && (sequential || isLayerStep(step)) ? `Add above ${step.label}` : 'Add on top';
	});

	const belowLayer: Layer = { ...newLayer('Below', blankCanvas(1, 1)), internal: true };
	const aboveLayer: Layer = { ...newLayer('Above', blankCanvas(1, 1)), internal: true };
	const STACK_MARK: HistoryMark = { kind: 'stack' };
	let blankTarget: Layer | null = null;
	let shadow: HTMLCanvasElement | null = null;
	let paintTarget = $state.raw<{ id: string | null; layer: Layer } | null>(null);
	let aboveShown = $state<string | null>(null);
	let shownTarget: { id: string | null; layer: Layer } | null = null;
	let paintCurrent = $state(false);

	let thumbOf = $derived.by(() => {
		void layerRevision;
		return (step: EditStep) => layerStore.thumb(step) ?? layerThumb(step);
	});

	function docSize(): { width: number; height: number } | null {
		return paintBase ? { width: paintBase.canvas.width, height: paintBase.canvas.height } : null;
	}

	$effect(() => {
		const id = paintTargetId;
		const base = paintBase;
		if (mode !== 'paint' || !base) return;
		const width = base.canvas.width;
		const height = base.canvas.height;
		return untrack(() => {
			if (!id) {
				if (!blankTarget || !isNormalized(blankTarget, width, height)) blankTarget = newLayer('Paint', blankCanvas(width, height));
				if (paintTarget?.layer !== blankTarget) paintTarget = { id: null, layer: blankTarget };
				return;
			}
			const step = stack.steps.find((entry) => entry.id === id);
			if (!step) return;
			const picture = picturePending?.id === id && !layerStore.has(id) ? picturePending : null;
			if (picture) {
				picturePending = null;
				const size = fitInside(picture.canvas, { width, height }, 0.6);
				const piece = copyCanvas(picture.canvas, size.width, size.height);
				const layer = newLayer(step.label, piece, Math.round((width - piece.width) / 2), Math.round((height - piece.height) / 2));
				layer.source = picture.canvas;
				layerStore.adopt(id, layer, width, height);
				paintTarget = { id, layer };
				paintSession?.setTool('transform');
				return;
			}
			let live = true;
			void layerStore
				.load(step, width, height)
				.then((layer) => {
					if (live && paintTargetId === id && paintTarget?.layer !== layer) paintTarget = { id, layer };
				})
				.catch((error) => (notice = describeMediaToolFailure(error)));
			return () => {
				live = false;
			};
		});
	});

	$effect(() => {
		const key = paintAboveKey;
		const list = untrack(() => paintAboveLayers);
		const size = docSize();
		if (!size) return;
		let live = true;
		void Promise.all(list.map((layer) => (layer.local ? docCanvas(layer.local, size.width, size.height) : canvasFromUrl(layer.url!))))
			.then((images) => {
				if (!live) return;
				if (aboveLayer.canvas.width !== size.width || aboveLayer.canvas.height !== size.height) aboveLayer.canvas = blankCanvas(size.width, size.height);
				const context = aboveLayer.canvas.getContext('2d');
				if (!context) return;
				context.clearRect(0, 0, size.width, size.height);
				images.forEach((image, index) => {
					const { rect, opacity } = list[index];
					context.globalAlpha = opacity;
					if (rect) context.drawImage(image, rect.x * size.width, rect.y * size.height, rect.width * size.width, rect.height * size.height);
					else context.drawImage(image, 0, 0, size.width, size.height);
				});
				context.globalAlpha = 1;
				aboveShown = `${key}#${size.width}x${size.height}`;
				paintSession?.refresh();
			})
			.catch(() => {
				if (live) aboveShown = null;
			});
		return () => {
			live = false;
		};
	});

	$effect(() => {
		const session = paintSession;
		const base = paintBase;
		const target = paintTarget;
		const shownAbove = aboveShown;
		const wanted = `${paintAboveKey}#${base?.canvas.width}x${base?.canvas.height}`;
		if (!session || !base || !target || shownAbove !== wanted || paintBaseKey !== paintBelowKey || target.id !== paintTargetId) {
			paintCurrent = false;
			return;
		}
		untrack(() => {
			const width = base.canvas.width;
			const height = base.canvas.height;
			if (shownTarget && shownTarget.layer !== target.layer) {
				const row = paintRow;
				settleLayer();
				paintRow = row;
			}
			if (belowLayer.canvas.width !== width || belowLayer.canvas.height !== height) belowLayer.canvas = blankCanvas(width, height);
			paintInto(belowLayer.canvas, base.canvas);
			syncTargetLook(target);
			const want = paintAboveLayers.length ? [belowLayer, target.layer, aboveLayer] : [belowLayer, target.layer];
			if (shownTarget?.layer !== target.layer || session.doc.layers[0] !== belowLayer || session.doc.width !== width || session.doc.height !== height) {
				session.showLayers(width, height, want, 1);
				shownTarget = target;
				shadow = copyCanvas(docCanvas(target.layer, width, height));
			} else {
				const extras = session.doc.layers.filter((layer) => layer !== belowLayer && layer !== target.layer && layer !== aboveLayer);
				session.doc.layers = [...want.slice(0, 2), ...extras, ...want.slice(2)];
				session.refresh();
			}
			paintCurrent = true;
		});
	});

	function syncTargetLook(target: { id: string | null; layer: Layer }) {
		const step = target.id ? stack.steps.find((entry) => entry.id === target.id) : null;
		target.layer.opacity = step ? opacityOf(step) : 1;
		target.layer.visible = step ? !step.hidden : true;
	}

	$effect(() => {
		const step = paintTargetStep;
		const target = paintTarget;
		if (!step || !target || target.id !== step.id) return;
		untrack(() => {
			syncTargetLook(target);
			paintSession?.refresh();
		});
	});

	$effect(() => {
		const steps = stack.steps;
		untrack(() => layerStore.sync(steps));
	});

	$effect(() => {
		const session = paintSession;
		if (!session) return;
		return session.subscribe(() => queueMicrotask(autoSettle));
	});

	$effect(() => {
		if (mode === 'paint') {
			untrack(() => {
				if (!paintSession) paintSession = new PaintSession();
			});
			return;
		}
		untrack(() => {
			settleLayer();
			shownTarget = null;
			paintTarget = null;
			paintCurrent = false;
			paintSession = null;
			paintState = null;
		});
	});

	function knownLayers(): Set<Layer> {
		return new Set([belowLayer, aboveLayer, ...(shownTarget ? [shownTarget.layer] : [])]);
	}

	function autoSettle() {
		const session = paintSession;
		const target = shownTarget;
		if (!session || !target || !paintCurrent || session.pointerActive || session.floatingLayer) return;
		const known = knownLayers();
		const extras = session.doc.layers.some((layer) => !known.has(layer));
		if (!extras && !session.history.canUndo) return;
		if (target.layer.source && !isNormalized(target.layer, session.docWidth, session.docHeight)) return;
		settleLayer();
	}

	function settleLayer() {
		const session = paintSession;
		const target = shownTarget;
		if (!session || !target || !session.ready) return;
		const width = session.docWidth;
		const height = session.docHeight;
		const known = knownLayers();
		session.placeFloating();
		const extras = session.doc.layers.filter((layer) => !known.has(layer));
		const normalized = isNormalized(target.layer, width, height);
		if (!session.history.canUndo && !extras.length && normalized) return;
		const fast = !extras.length && normalized && session.history.pixelsOnly;
		const commands = session.history.drain();
		const pixels: Command[] = [];
		if (fast) pixels.push(...commands);
		else {
			const command = diffCommand(target.layer, shadow ?? blankCanvas(width, height), width, height);
			if (command) pixels.push(command);
		}
		let id = target.id;
		if (pixels.length && !id) id = materialize(target, width, height);
		if (id && pixels.length) {
			recordMarks(pixels.map((command) => ({ kind: 'pixels' as const, id: id!, command })));
			layerStore.touch(id);
		}
		for (const extra of extras) keepPieceAsLayer(extra, id, width, height);
		if (extras.length || !fast) session.showLayers(width, height, session.doc.layers.includes(aboveLayer) ? [belowLayer, target.layer, aboveLayer] : [belowLayer, target.layer], 1);
		shadow = copyCanvas(docCanvas(target.layer, width, height));
	}

	function layerAnchorOf(row: string | null): string | null {
		if (row === ORIGINAL_ROW) return '';
		const found = row ? stack.steps.findIndex((step) => step.id === row) : -1;
		if (found >= 0 && found < stack.cursor && (sequential || isLayerStep(stack.steps[found]))) return row;
		return sequential ? null : (appliedSteps(stack).findLast(isLayerStep)?.id ?? null);
	}

	function insertLayer(name: string, row: string | null): string {
		const params: PaintParams = { layer: '', url: '', layers: 1, name };
		const before = stack.seq;
		stack = insertSteps(discardPending(stack), [paintDraft(params, { width: 0, height: 0 }) as StepDraft<unknown>], layerAnchorOf(row));
		noteStack();
		return `s${before + 1}`;
	}

	function materialize(target: { id: string | null; layer: Layer }, width: number, height: number): string {
		const id = insertLayer(nextPaintName(stack.steps.map((step) => step.label)), paintRow);
		layerStore.adopt(id, target.layer, width, height);
		target.id = id;
		blankTarget = null;
		paintRow = id;
		return id;
	}

	function keepPieceAsLayer(piece: Layer, after: string | null, width: number, height: number) {
		normalizeLayer(piece, width, height);
		const id = insertLayer(nextPaintName(stack.steps.map((step) => step.label)), after);
		layerStore.adopt(id, piece, width, height);
		paintRow = id;
	}

	function recordMarks(marks: HistoryMark[]) {
		undoMarks = [...undoMarks, ...marks].slice(-400);
		redoMarks = [];
	}

	function noteStack() {
		stackSig = stackSigOf(stack);
		recordMarks([STACK_MARK]);
	}

	function resetLayers() {
		layerStore.dispose();
		undoMarks = [];
		redoMarks = [];
		shownTarget = null;
		paintTarget = null;
		blankTarget = null;
		layerRevision += 1;
	}

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

	const stackSigOf = (value: EditStackState<unknown>) => `${value.cursor}:${value.steps.length}:${value.revisions?.length ?? 0}:${value.redoRevisions?.length ?? 0}`;
	let stackSig = stackSigOf(untrack(() => stack));
	let sessionUndo = $derived(mode === 'paint' && paintState?.canUndo ? (paintState.undoLabel ?? 'stroke') : null);
	let sessionRedo = $derived(mode === 'paint' && paintState?.canRedo ? (paintState.redoLabel ?? 'stroke') : null);
	let pixelUndo = $derived(topPixels(undoMarks));
	let pixelRedo = $derived(topPixels(redoMarks));
	let sliceUndo = $derived(activeTool === 'slice' && sliceHistory.past.length ? sliceHistory.labels[sliceHistory.labels.length - 1] || 'slice edit' : null);
	let sliceRedo = $derived(activeTool === 'slice' && sliceHistory.future.length ? sliceHistory.futureLabels[0] || 'slice edit' : null);
	let maskUndo = $derived(mode === 'mask' && maskHistory.canUndo);
	let undoText = $derived(sessionUndo ?? pixelUndo ?? (maskUndo ? 'marked area' : (sliceUndo ?? undoLabel(stack))));
	let canUndoNow = $derived(!!sessionUndo || !!pixelUndo || !!sliceUndo || maskUndo || canUndo(stack));
	let canRedoNow = $derived(!!sessionRedo || !!pixelRedo || (mode === 'mask' && maskHistory.canRedo) || !!sliceRedo || canRedo(stack));
	let redoText = $derived(sessionRedo ?? pixelRedo ?? sliceRedo ?? redoLabel(stack));

	function topPixels(marks: HistoryMark[]): string | null {
		const mark = marks[marks.length - 1];
		return mark?.kind === 'pixels' ? mark.command.label : null;
	}
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
		const current = stack;
		untrack(() => {
			let next = sliceLast(current);
			if (sequential && settledLayered(next)) sequential = false;
			if (!sequential) next = arrangeLayers(next);
			if (next !== current) stack = next;
		});
	});

	async function useLayerOrder() {
		const ok = await confirmDialog({
			title: 'Switch to layers?',
			message: 'Edits will apply to the whole layered picture, and layers painted after a crop or resize are placed on the original picture, so the result may change. Undo brings the old order back.',
			confirmLabel: 'Switch to layers',
			cancelLabel: 'Keep the old order'
		});
		if (!ok || !(await leaveTool())) return;
		stack = switchToLayers(stack);
		sequential = false;
	}

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
		resetLayers();
		stack = sliceLast(next.stack);
		sequential = !layerOrdered(stack);
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

	onMount(() => {
		host.setCloseGuard?.(async () => {
			const layersFlushed = untrack(() => layerStore.unsaved) === 0 || (await layerStore.flush());
			const edits = untrack(() => unsavedEdits(stack, null));
			if (edits > 0) await autosave.settle();
			return editCloseLoss({ edits, layersFlushed, status: autosave.status() });
		});
		return () => host.setCloseGuard?.(null);
	});

	let lastDirty = -1;
	$effect(() => {
		const count = autosaveStatus === 'saved' && !unsavedLayers ? 0 : dirtyCount;
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

	let mainDraft = $derived.by(() => {
		void layerRevision;
		return mode === 'paint' ? null : layerStore.display(mainSteps);
	});
	let mainKey = $derived(mainDraft ? stepsKey(mainDraft) : null);
	let mainList = $derived.by(() => {
		void mainKey;
		return untrack(() => mainDraft);
	});
	$effect(() => track(mainList, (value) => (mainRender = value), undefined, true));
	$effect(() => track(beforeList && layerStore.display(beforeList), (value) => (beforeRender = value)));
	$effect(() => track(thumbList && layerStore.display(thumbList), (value) => (thumbRender = value)));
	$effect(() => {
		const key = paintBelowKey;
		return track(
			paintBelow,
			(value) => {
				paintBase = value;
				paintBaseKey = value ? key : null;
			},
			4096
		);
	});

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

	function toggleStepVisibility(step: EditStep) {
		return isLayerStep(step) ? toggleHiddenUndoable(stack, step.id) : toggleHidden(stack, step.id);
	}

	function settleLive() {
		if (liveTimer) clearTimeout(liveTimer);
		liveTimer = null;
		if (isLive(stack)) stack = commitLive(stack);
	}

	function writeLive(next: EditStackState<unknown>, options: { hold?: boolean } = {}) {
		notice = null;
		stack = next;
		if (options.hold) return;
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

	function paintNameOf(step: EditStep): string {
		return isFlattenedPaint(step) ? 'Paint' : ((step.params as PaintParams).name ?? step.label);
	}

	async function pixelsOf(step: EditStep): Promise<HTMLCanvasElement | null> {
		const url = (step.params as PaintParams).url;
		return layerStore.canvasOf(step.id) ?? (url ? await canvasFromUrl(url) : null);
	}

	async function duplicateLayerStep(id: string) {
		if (busy) return;
		settleLayer();
		const step = stack.steps.find((candidate) => candidate.id === id);
		if (!step || step.tool !== 'paint') return;
		const taken = new Set(stack.steps.map((candidate) => candidate.label));
		const base = `${paintNameOf(step)} copy`;
		let name = base;
		for (let n = 2; taken.has(name); n += 1) name = `${base} ${n}`;
		const params: PaintParams = { ...(step.params as PaintParams), name, layers: 1 };
		const copy = paintDraft(params, { width: 0, height: 0 });
		const seq = stack.seq;
		stack = duplicateStep(stack, id, { label: copy.label, summary: copy.summary, params: params as unknown });
		if (stack.seq === seq) return;
		noteStack();
		layerStore.copy(id, `s${stack.seq}`, params.url);
	}

	function openPaintOn(id: string | null) {
		paintRow = id;
		notice = null;
		compare = false;
		if (!paintActive) {
			const entry = paintEntryId('brush');
			activeTool = entry;
			host.setActiveTool(entry);
		}
		if (phone) stackOpen = false;
	}

	async function addEmptyLayer() {
		if (busy || !source) return;
		const row = selectedStepId;
		if (mode === 'paint') settleLayer();
		else if (!(await leaveTool())) return;
		openPaintOn(insertLayer(nextPaintName(stack.steps.map((step) => step.label)), row));
	}

	let addRow: string | null = null;

	function startPictureLayer(from: 'library' | 'history' | 'file') {
		if (busy || !source) return;
		addRow = selectedStepId;
		addSource = from;
	}

	async function placePicture(image: { canvas: HTMLCanvasElement; name: string; title?: string | null }) {
		addSource = null;
		if (mode === 'paint') settleLayer();
		else if (!(await leaveTool())) return;
		const id = insertLayer(image.title?.trim() || nextPictureName(stack.steps.map((step) => step.label)), addRow);
		picturePending = { id, canvas: image.canvas };
		openPaintOn(id);
	}

	function renameLayer(id: string, label: string) {
		const step = stack.steps.find((candidate) => candidate.id === id);
		if (!step) return;
		const patch: Partial<StepDraft<unknown>> = step.tool === 'paint' && !isFlattenedPaint(step) ? { params: { ...(step.params as PaintParams), name: label } as unknown } : {};
		stack = renameStep(stack, id, label, patch);
		const layer = layerStore.layer(id);
		if (layer) layer.name = label;
	}

	async function mergeLayerDown(id: string) {
		if (busy) return;
		settleLayer();
		const index = stack.steps.findIndex((candidate) => candidate.id === id);
		const upperId = stack.steps[index]?.id;
		const lowerId = stack.steps[index - 1]?.id;
		if (!upperId || !lowerId || stack.steps[index].tool !== 'paint' || stack.steps[index - 1].tool !== 'paint' || index > stack.cursor - 1) return;
		busy = true;
		try {
			if (!(await layerStore.flush([upperId, lowerId]))) return;
			const upper = stack.steps.find((step) => step.id === upperId)!;
			const lower = stack.steps.find((step) => step.id === lowerId)!;
			const below = await pixelsOf(lower);
			const above = await pixelsOf(upper);
			const sized = below ?? above;
			if (!sized) {
				stack = mergeInto(stack, lower.id, upper.id, lower.params);
				noteStack();
				return;
			}
			const { width, height } = sized;
			const pixels = (canvas: HTMLCanvasElement | null) => (canvas ? copyCanvas(canvas, width, height) : blankCanvas(width, height)).getContext('2d')!.getImageData(0, 0, width, height);
			const merged = mergeLayerPixels(pixels(below).data, opacityOf(lower), pixels(above).data, opacityOf(upper));
			const out = blankCanvas(width, height);
			out.getContext('2d')!.putImageData(new ImageData(merged, width, height), 0, 0);
			const artifact = await uploadMediaToolArtifact(await canvasToPngFile(out, 'paint-layer.png'));
			const params: PaintParams = { layer: artifact.id, url: artifact.url, layers: 1, name: paintNameOf(lower) };
			stack = mergeInto(stack, lower.id, upper.id, params as unknown);
			noteStack();
			layerStore.replace(lower.id, out, artifact.url);
			if (paintRow === upper.id) paintRow = lower.id;
		} catch (error) {
			notice = describeMediaToolFailure(error);
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
			mask = maskChannelOf(stored.data, geometryKey(maskBasis));
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
		if (mode === 'paint') {
			settleLayer();
			return true;
		}
		if (mode === 'mask') return landMask();
		landSlice();
		sliceAdding = false;
		if (stack.pending) stack = sliceLast(apply(stack));
		return true;
	}

	function paintRowFor(row: string | null): string | null {
		const step = row ? appliedSteps(stack).find((entry) => entry.id === row) : null;
		return step && (sequential || isLayerStep(step)) ? step.id : null;
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
		const row = paintRowFor(selectedStepId) ?? paintRowFor(paintRow);
		if (!(await leaveTool())) return;
		notice = null;
		compare = false;
		activeTool = id;
		host.setActiveTool(id);
		if (isPaintEntry(id)) paintRow = row;
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
		if (step.tool === 'paint' || (mode === 'paint' && isLayerStep(step))) {
			if (mode === 'paint') settleLayer();
			else if (!(await leaveTool())) return;
			openPaintOn(id);
			return;
		}
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

	$effect(() => {
		const sig = stackSigOf(stack);
		untrack(() => {
			if (sig === stackSig) return;
			stackSig = sig;
			recordMarks([STACK_MARK]);
		});
	});

	function stepPixels(direction: 'undo' | 'redo'): boolean {
		let from = direction === 'undo' ? undoMarks : redoMarks;
		let done: HistoryMark | null = null;
		while (from.length) {
			const mark = from[from.length - 1];
			if (mark.kind !== 'pixels') break;
			from = from.slice(0, -1);
			if (!layerStore.layer(mark.id) || !stack.steps.some((step) => step.id === mark.id)) continue;
			if (direction === 'undo') mark.command.undo();
			else mark.command.redo();
			done = mark;
			break;
		}
		if (direction === 'undo') {
			undoMarks = from;
			if (done) redoMarks = [...redoMarks, done];
		} else {
			redoMarks = from;
			if (done) undoMarks = [...undoMarks, done];
		}
		if (!done || done.kind !== 'pixels') return false;
		layerStore.touch(done.id);
		if (shownTarget?.id === done.id && paintSession) {
			shadow = copyCanvas(docCanvas(shownTarget.layer, paintSession.docWidth, paintSession.docHeight));
			paintSession.refresh();
		}
		return true;
	}

	function stepStack(direction: 'undo' | 'redo') {
		const held = !!stack.pending;
		stack = direction === 'undo' ? undo(stack) : redo(stack);
		stackSig = stackSigOf(stack);
		const marks = direction === 'undo' ? undoMarks : redoMarks;
		if (held || marks[marks.length - 1]?.kind !== 'stack') return;
		if (direction === 'undo') {
			undoMarks = undoMarks.slice(0, -1);
			redoMarks = [...redoMarks, STACK_MARK];
		} else {
			redoMarks = redoMarks.slice(0, -1);
			undoMarks = [...undoMarks, STACK_MARK];
		}
	}

	function doUndo() {
		if (sessionUndo && paintSession) {
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
		if (stepPixels('undo')) return;
		const unswitch = stack.cleared?.label === SWITCH_TO_LAYERS && stack.cleared.after === stack.steps;
		stepStack('undo');
		if (unswitch) sequential = true;
	}

	function doRedo() {
		if (sessionRedo && paintSession) {
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
		if (stepPixels('redo')) return;
		stepStack('redo');
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
			settleLayer();
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

	function clearSlice() {
		sliceSelected = [];
		setSlice({ ...sliceParams, cells: [], layout: { ...sliceParams.layout, preset: null, detected: null } }, 'clear');
	}

	function cancelCrop() {
		if (mode === 'crop') closeActiveTool();
	}

	async function editSelectedArea() {
		const session = paintSession;
		const selection = session?.selection;
		const target = railTools.list('image').find((entry) => entry.task === 'edit_region');
		if (!session || !selection || !target) return;
		maskSeed = { path: selection.path.map((point) => ({ x: point.x, y: point.y })), width: session.doc.width, height: session.doc.height };
		await selectTool(target.id);
		if (activeTool !== target.id) {
			maskSeed = null;
			return;
		}
		await tick();
		focusDockPrompt();
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
		if (activeTool === 'slice') {
			sliceSelected = [];
			sliceAdding = false;
			if (pending?.tool === 'slice') sliceHistory = record(sliceHistory, sliceParams, 'reset');
		}
		if (pending) stack = discardPending(stack);
	}

	const ADD_LAYER_ITEMS: MenuItem[] = [
		{ id: 'new', label: 'Empty layer', icon: 'editor-tool-brush' },
		{ id: 'sep', label: '', separator: true },
		{ id: 'library', label: 'Picture from Library', icon: 'image' },
		{ id: 'history', label: 'Picture from History', icon: 'clock' },
		{ id: 'file', label: 'Picture from a file', icon: 'paint-add-image' }
	];

	const FLATTENED_NOTE_KEY = 'potionui:image-editor:flattened-paint-note';
	let flattenedNoteDismissed = $state(readFlattenedNote());
	let flattenedPaintNote = $derived(!flattenedNoteDismissed && applied.some(isFlattenedPaint));

	function readFlattenedNote(): boolean {
		try {
			return localStorage.getItem(FLATTENED_NOTE_KEY) === '1';
		} catch {
			return false;
		}
	}

	function dismissFlattenedNote() {
		flattenedNoteDismissed = true;
		try {
			localStorage.setItem(FLATTENED_NOTE_KEY, '1');
		} catch {
			return;
		}
	}

	function enterKey() {
		if (mode === 'paint') {
			settleLayer();
			return;
		}
		return applyTool();
	}

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
		if (event.key === 'Escape') {
			if (event.defaultPrevented) return;
			const handled = handleEscape({
				mode,
				paint: paintSession && paintState ? { session: paintSession, state: paintState } : null,
				cropPending: pendingHere?.tool === 'crop',
				cancelCrop
			});
			if (handled) {
				event.preventDefault();
				event.stopPropagation();
			}
			return;
		}
		const entries = railGroups.flatMap((group) => group.tools.filter((tool) => isPaintEntry(tool.id) && !tool.disabled && tool.keys).map((tool) => ({ id: tool.id, key: tool.keys })));
		const id = paintToolForKey(event, entries);
		if (!id) return;
		event.preventDefault();
		event.stopPropagation();
		void selectTool(id);
	}

	async function prepareSave(): Promise<boolean> {
		if (!(await leaveTool())) return false;
		if (await layerStore.flush()) return true;
		notice = 'A layer could not be saved. Try again.';
		return false;
	}

	function itemName(item: MediaToolItem | undefined): string {
		return item ? (item.original_filename ?? item.filename) : 'The copy';
	}

	function saveName(): string | null {
		return saveNameFor(host.tabId, source);
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
				body: runBody(payload, ref, saveMode, saved?.itemId ?? null, null, editLink, saveName())
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
				body: runBody(payload, ref, 'new', null, params.collection, null, saveName())
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
			(mode === 'mask'
					? maskDirty
					: activeTool === 'slice'
						? pendingHere?.tool === 'slice' && !sliceLocked
						: looksOrCard
							? !!pendingHere && canApplyEffect(effectParams, effectOriginal, effectUpdating)
							: registeredTool && applyOverride?.tool === activeTool
								? applyOverride.enabled
								: !!pendingHere || !!catalogTool)
	);
	const overlayRevision = overlays.revision;
	let canvasSurface = $derived.by<ImageCanvasSurface>(() => {
		void $overlayRevision;
		const taskId = registeredTool?.task;
		return {
			paint: paintActive && paintSession && paintState ? { session: paintSession, state: paintState } : null,
			mask:
				mode === 'mask'
					? maskControls({
							entries: maskToolEntries(),
							state: $maskToolState,
							erase: maskErase,
							brush: maskBrush,
							hasArea: !!$maskArea,
							canFocusPrompt: !!taskId && tasks.get('image', taskId)?.prompt !== 'none',
							onBrush: (value) => (maskBrush = value),
							onErase: (erase) => (maskErase = erase),
							onClear: resetTool,
							onInvert: () => maskLayer?.invert(),
							onChoose: chooseOverlay
						})
					: null,
			crop: cropInput ? { params: cropParams, input: cropInput, canApply, change: (next) => setDraft('crop', next), apply: () => void applyTool(), cancel: cancelCrop } : null,
			slice: activeTool === 'slice' ? { locked: sliceLocked, hasCells: sliceParams.cells.length > 0, detect: runDetect, clear: clearSlice } : null,
			editArea: () => void editSelectedArea()
		};
	});
	let canvasActions = $derived.by(() => {
		void $editorsRevision;
		if (!source || busy || !registeredTool) return [];
		return canvasActionsOf(registeredTool, { editor: 'image', tool: registeredTool.id, phone, host: toolHost, surface: canvasSurface });
	});
	let canReset = $derived(
		!busy &&
			(liveEntry
				? appliedSet.has(activeTool!)
				: looksOrCard
					? canResetEffect(effectParams, $filterCatalog.items, stillEffects)
					: !!pendingHere || mode === 'mask')
	);
	let phoneBarred = $derived(phone && activeTool === 'effects');
	let pendingTools = $derived([
		...(pending ? [pending.tool === 'effects' && effectCard && activeTool ? activeTool : pending.tool] : []),
		...(maskDirty ? ['mask'] : [])
	]);
	let originalSummary = $derived(sourceSize ? `${sourceSize.width}×${sourceSize.height} · ${extension}` : extension);
	let stackMore = $derived(applied.length ? [{ id: 'clear', label: 'Remove all edits', icon: 'trash' }] : []);

	let currentStep = $derived(applied.length && !stack.pending ? applied[applied.length - 1] : null);
	let sliceSaveMode = $derived(!!saveSlice || referencesOnly);
	let statusInfo = $derived(
		statusParts({
			tool: activeTool,
			areaTool: (taskTool && !growsTool) || activeTool === 'mask',
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
	let footerActions = $derived(!paintActive && (phone ? !phoneBarred && !taskTool : !taskTool && !!activeTool));

	const menuCommands: EditorMenuCommands = {
		save: (action) => onSave(action),
		closeEditor: () => requestCloseTab(host.tabId),
		replaceWith: (id) => replacePicker?.open(id as SourceEntryId),
		openRecent: (id) => void openRecentEdit(id),
		openAllEdits: () => (recentDialog = true),
		undo: () => doUndo(),
		redo: () => doRedo(),
		apply: () => void applyTool(),
		reset: () => resetTool(),
		toggleStepHidden: () => {
			const step = currentStep;
			if (step) stack = toggleStepVisibility(step);
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
		apply: activeTool && !taskTool && !liveEntry && !paintActive ? { label: applyLabel, enabled: canApply, reason: 'Nothing to apply yet' } : null,
		reset: !activeTool || paintActive
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
	{#if registeredTool?.options || registeredTool?.schema}
		{#if registeredTool.task && !growsTool}
			<MaskOptions bind:brush={maskBrush} bind:erase={maskErase} onclear={resetTool} what={maskWhatOf(registeredTool.task)} />
			{#if staleMask}
				<p class="text-sm text-warning" data-mask-stale>The marked area was drawn before a shape change, so it no longer lines up. Mark it again.</p>
			{/if}
		{/if}
		{#if registeredTool.schema}
			<ToolEntryPanel entry={registeredTool} entryKey={entryKey('image', registeredTool.id)} host={toolHost} {phone} onclose={closeActiveTool} />
		{:else if registeredTool.options}
			<EditorEntryComponent entry={entryKey('image', registeredTool.id)} componentRef={registeredTool.options} props={{ host: toolHost, phone, tool: registeredTool.id }} onclose={closeActiveTool} />
		{/if}
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
			<PaintOptions session={paintSession} state={paintState} {phone} layers={false} target={{ name: paintTargetStep ? paintNameOf(paintTargetStep) : null, thumb: paintTargetStep ? thumbOf(paintTargetStep) : null }} />
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
			onclear={clearSlice}
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
			<div data-field-name="intensity">
				<SliderField name="intensity" config={intensityConfig} value={lookParams.intensity} onChange={(_name, value) => setDraft('effects', { ...lookParams, intensity: Number(value) })} />
			</div>
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
				<div class="min-w-0 flex-1" data-field-name="intensity">
					<SliderField name="intensity" config={{ ...intensityConfig, layout: 'inline' }} value={lookParams.intensity} onChange={(_name, value) => setDraft('effects', { ...lookParams, intensity: Number(value) })} />
				</div>
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
		{#if paintSession}
			{#key paintSession}
				<PaintStage
					session={paintSession}
					tool={paintToolOfEntry(activeTool!)}
					zoomControl={!phone}
					locked={busy || !paintCurrent}
					onstate={(session, snapshot) => {
						if (session === paintSession) paintState = snapshot;
					}}
				/>
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
							seed={maskSeed}
							onseeded={() => (maskSeed = null)}
							{phone}
							onstroke={(painted) => {
								if (painted) maskDirty = true;
							}}
							onhistory={(next) => (maskHistory = next)}
						/>
					{/key}
				{/if}
				{#if shownMask}
					<PatchMaskOverlay mask={shownMask} />
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
		{#if taskTool && phone && runningId && runStatus}
			<div class="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2" data-run-chip-host>
				<RunChip status={runStatus === 'queued' ? 'queued' : 'running'} progress={runView?.progress ?? 0} onclick={() => void generate.cancel(runningId!)} />
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

{#snippet dockTempRow()}
	{#if runStep && runStatus === 'done' && runStep.params.results.length && !runOutOfDate}
		<CandidateStrip dock results={runStep.params.results} chosen={runStep.params.chosen} onchoose={(index) => generate.choose(runStep!.step.id, index)} onagain={() => void generate.tryAgain(runStep!.step.id)} />
	{:else if runStep && runStatus === 'done' && runOutOfDate}
		<DockStaleNotice onredo={() => void redoStep(runStep!.step.id)} />
	{/if}
{/snippet}

{#snippet dockExtrasView()}
	{#if registeredTool?.dockExtras}
		<EditorEntryComponent entry={entryKey('image', registeredTool.id)} componentRef={registeredTool.dockExtras} props={{ host: toolHost, tool: registeredTool.id }} />
	{/if}
{/snippet}

{#snippet dockContent()}
	{#if dockRef}
		<EditorEntryComponent
			entry={entryKey('image', registeredTool!.id)}
			componentRef={dockRef}
			props={{ running: runningId, run: runStep && runStatus ? { status: runStatus, progress: runView?.progress ?? null } : null, large: largeArea, settings: promptSettings, tempRow: dockTempRow, hasTemp: dockHasTemp, extras: registeredTool?.dockExtras ? dockExtrasView : undefined }}
		/>
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
		history={{ canUndo: canUndoNow, canRedo: canRedoNow, undoLabel: canUndoNow ? undoText : null, redoLabel: canRedoNow ? redoText : null }}
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
		onoriginal={paintActive ? () => openPaintOn(ORIGINAL_ROW) : undefined}
		onseek={(cursor) => (stack = seek(stack, cursor))}
		ontogglehidden={(id) => {
			const step = stack.steps.find((candidate) => candidate.id === id);
			if (step) stack = toggleStepVisibility(step);
		}}
		layered
		duplicable={(step) => step.tool === 'paint'}
		mergeable={(upper, lower) => upper.tool === 'paint' && !upper.hidden && lower?.tool === 'paint' && !lower.hidden}
		onduplicate={(id) => void duplicateLayerStep(id)}
		onmerge={(id) => void mergeLayerDown(id)}
		onrename={renameLayer}
		selectedId={selectedStepId}
		isLayer={isLayerStep}
		layerThumb={thumbOf}
		movable={!sequential}
		maskOf={patchMaskOf}
		{showMask}
		ontogglemask={() => (showMask = !showMask)}
		onopacity={(id, opacity, live) => (stack = setOpacity(stack, id, opacity, live))}
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
		{#snippet headerActions()}
			{#if source}
				<Menu label="Add a layer" icon="plus" size="xs" align="right" width={phone ? 280 : 260} menuAttrs={{ 'data-layers-add-menu': '' }}>
					{#snippet trigger({ open, toggle })}
						<IconButton icon="plus" label="Add a layer" size="xs" class={phone ? '!min-h-11 !min-w-11' : ''} ariaExpanded={open} onclick={toggle} data-layers-add />
					{/snippet}
					{#snippet children({ close })}
						<p class="px-2.5 pb-1 pt-1.5 font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle" data-layers-add-heading>{addAboveLabel}</p>
						{#each ADD_LAYER_ITEMS as item (item.id)}
							{#if item.separator}
								<div class="my-1 h-px bg-line" role="separator"></div>
							{:else}
								<button
									type="button"
									role="menuitem"
									class="flex w-full items-center gap-2.5 rounded px-2.5 text-left text-sm text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none {phone ? 'h-12' : 'h-9'}"
									onclick={() => {
										close(true);
										if (item.id === 'new') void addEmptyLayer();
										else void startPictureLayer(item.id as 'library' | 'history' | 'file');
									}}
									data-layers-add-item={item.id}
								>
									{#if item.icon}<Icon name={item.icon} className="h-4 w-4 flex-shrink-0" />{/if}
									<span class="min-w-0 flex-1 truncate">{item.label}</span>
								</button>
							{/if}
						{/each}
					{/snippet}
				</Menu>
			{/if}
		{/snippet}
		{#snippet notes()}
			{#if sequential && !sequentialNoteDismissed && !layerOrdered(stack)}
				<div class="mx-2 mb-2.5 mt-1 flex flex-none items-start gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" data-stack-note="sequential">
					<Icon name="info" className="mt-px h-3.5 w-3.5 flex-none" />
					<span class="flex min-w-0 flex-1 flex-col items-start gap-1.5">
						<span>Made before layers and edits were split: each edit here still applies only to what is beneath it.</span>
						<Button size="xs" onclick={() => void useLayerOrder()} attrs={{ 'data-stack-switch-layers': '' }}>Switch to layers</Button>
					</span>
					<IconButton icon="close" label="Dismiss" size="xs" onclick={() => (sequentialNoteDismissed = true)} data-stack-note-dismiss />
				</div>
			{/if}
			{#if flattenedPaintNote}
				<div class="mx-2 mb-2.5 mt-1 flex flex-none items-start gap-2 rounded bg-surface-2 px-2.5 py-2 text-sm text-fg-muted" data-stack-note="flattened-paint">
					<Icon name="info" className="mt-px h-3.5 w-3.5 flex-none" />
					<span class="min-w-0 flex-1">An earlier version saved this paint as one layer. It opens as a single layer you can still fade or move.</span>
					<IconButton icon="close" label="Dismiss" size="xs" onclick={dismissFlattenedNote} data-stack-note-dismiss />
				</div>
			{/if}
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

{#if recentDialog}
	<RecentEditsDialog tabId={host.tabId} inPlace={false} onclose={() => (recentDialog = false)} />
{/if}

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
			dock={taskTool && dockRef && !phone ? dockContent : undefined}
			{phone}
			bind:compare
			bind:stackOpen
			canCompare={canCompareNow}
			{canvasActions}
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
						<FloatingPrompt phone running={runningId} large={largeArea} settings={promptSettings} />
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
	{#if addSource}
		<ImageSourceMenu mode="layer" start={addSource} onPick={(image) => void placePicture(image)} onClose={() => (addSource = null)} />
	{/if}
</div>
