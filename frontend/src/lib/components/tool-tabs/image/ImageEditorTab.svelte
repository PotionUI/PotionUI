<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import { pickFromUpload } from '$lib/components/form-fields/mediaLoaderPicks';
	import { canvasToPngFile } from '$lib/components/imageEditor/flatten';
	import type { PaintSession, SessionSnapshot } from '$lib/components/imageEditor/session';
	import { Button, SegmentedControl, Spinner } from '$lib/components/ui';
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
	import { filterCatalog, loadFilterCatalog } from '$lib/filters/catalog';
	import { getOp } from '$lib/filters/engine';
	import { scalarParams } from '$lib/filters/steps';
	import { loadPresetFormSchema } from '$lib/form/formSources';
	import { loadImage } from '$lib/media/editors/loadImage';
	import { resolveEditableResource } from '$lib/media/editors/editorSource';
	import { EMPTY_IMAGE_PLAN, planOutputSize } from '$lib/media/editors/imageEditPlan';
	import { displaySize } from '$lib/media/editors/cropGeometry';
	import { loadMediaToolCatalog, mediaToolCatalog } from '$lib/media-tools/catalog';
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
	import { toasts } from '$lib/stores/toast';
	import { landOnHistoryResult } from '$lib/tools/historyResult';
	import {
		apply,
		appliedCount,
		appliedSteps,
		beforeSteps,
		beginEdit,
		canRedo,
		canUndo,
		createStack,
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
		type EditStackState,
		type EditStep,
		type StepDraft
	} from '$lib/tool-tabs/frame/editStack';
	import type { RailGroup, SaveAction, SavingProgress } from '$lib/tool-tabs/frame/types';
	import { fitViewport } from '$lib/tool-tabs/frame/viewport';
	import { geometryKey, liveMask, maskIsStale, sendPatch, type MaskChannel } from '$lib/tool-tabs/image/mask';
	import { inputRef, outputSize, replaceOption, runBody, sourceOrigin, type PixelSize, type SourceOrigin } from '$lib/tool-tabs/image/payload';
	import { FULL_SIDE, ImageRenderer, bakeIsCapped, isAbort, orient, stepsKey, type Rendered } from '$lib/tool-tabs/image/render';
	import { imageFieldsOf, sendTargetsFor, type ImageField } from '$lib/tool-tabs/image/sendTargets';
	import { artifactRef, pastedImage } from '$lib/tool-tabs/image/sourceRefs';
	import { readImageState, stackSignature, unsavedEdits, writeImageState, type SavedCopy } from '$lib/tool-tabs/image/state';
	import {
		ADJUST_OPS,
		CATALOG_TOOL,
		IMAGE_PINNED_LANES,
		adjustDraft,
		catalogDraft,
		cropDraft,
		imageRail,
		isCoreTool,
		lookDraft,
		paintDraft,
		resizeDraft,
		toolMeta,
		type CatalogParams,
		type CropParams,
		type EffectParams,
		type PaintParams,
		type ResizeParams
	} from '$lib/tool-tabs/image/steps';
	import { mediaFileStem } from '$lib/tool-tabs/sources';
	import type { ToolTabHost } from '$lib/tool-tabs/types';
	import { isGenerationTab, type GenerationTab, type MediaRef } from '$lib/types/tabs';
	import CatalogToolOptions from './CatalogToolOptions.svelte';
	import CropOptions, { aspectLabel, IMAGE_ASPECTS } from './CropOptions.svelte';
	import CropOverlay from './CropOverlay.svelte';
	import EffectsOptions from './EffectsOptions.svelte';
	import ImageEmptyState from './ImageEmptyState.svelte';
	import MaskLayer from './MaskLayer.svelte';
	import PaintOptions from './PaintOptions.svelte';
	import PaintStage from './PaintStage.svelte';
	import ResizeOptions from './ResizeOptions.svelte';

	type ImageHint = { look?: string; effect?: string; orientation?: 'rotate' | 'flip'; sendTo?: { tabId: string; field: string } };
	type HintedHost = ToolTabHost & { readonly hint?: ImageHint | null; clearHint?: () => void };

	let { host }: { host: ToolTabHost } = $props();

	function knownTool(id: string | null | undefined): string | null {
		if (!id) return null;
		return isCoreTool(id) || id.includes('.') ? id : 'crop';
	}

	const initial = untrack(() => readImageState(host.state, knownTool(host.activeTool)));
	let stack = $state.raw<EditStackState<unknown>>(initial.stack);
	let activeTool = $state<string | null>(knownTool(initial.activeTool));
	let sendFirst = $state.raw<{ tabId: string; field: string } | null>(null);
	let viewport = $state(initial.viewport);
	let mask = $state.raw<MaskChannel | null>(initial.mask);
	let saved = $state.raw<SavedCopy | null>(initial.saved);
	let compare = $state(false);
	let stackOpen = $state(false);
	let saveMenuOpen = $state(false);
	let saving = $state<SavingProgress | null>(null);
	let busy = $state(false);
	let notice = $state<string | null>(null);

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

	let paintStage = $state<ReturnType<typeof PaintStage> | undefined>();
	let paintSession = $state.raw<PaintSession | null>(null);
	let paintState = $state.raw<SessionSnapshot | null>(null);
	let maskLayer = $state<ReturnType<typeof MaskLayer> | undefined>();
	let maskBrush = $state(48);
	let maskErase = $state(false);
	let maskDirty = $state(false);

	let presetNames = $state<Record<string, string>>({});
	let fieldsByTab = $state<Record<string, ImageField[]>>({});

	onMount(() => {
		void loadMediaToolCatalog().catch(() => undefined);
		void loadPresetNameMap()
			.then((names) => (presetNames = names))
			.catch(() => undefined);
		if (!activeTool && source) activeTool = 'crop';
	});

	let catalog = $derived($mediaToolCatalog);
	let railGroups = $derived<RailGroup[]>(
		source
			? imageRail(catalog)
			: imageRail(catalog).map((group) => ({
					...group,
					tools: group.tools.map((tool) => ({ ...tool, disabled: true, reason: 'Open a picture first' }))
				}))
	);
	let effective = $derived(effectiveSteps(stack));
	let applied = $derived(appliedSteps(stack));
	let editCount = $derived(appliedCount(stack));
	let dirtyCount = $derived(unsavedEdits(stack, saved) + (activeTool === 'paint' && paintState?.dirty ? 1 : 0));
	let replace = $derived(replaceOption(origin));
	let fileName = $derived(source ? (source.name ?? mediaFileStem(source) ?? 'Picture') : null);
	let stem = $derived(mediaFileStem(source) ?? 'picture');
	let extension = $derived((fileName?.split('.').pop() ?? 'png').toUpperCase());
	let liveMaskNow = $derived(liveMask(mask, effective));
	let staleMask = $derived(maskIsStale(mask, effective));

	let pending = $derived(stack.pending);
	let pendingHere = $derived(pending && pending.tool === activeTool ? pending : null);
	let targetIndex = $derived(pending?.target ? effective.findIndex((step) => step.id === pending!.target) : -1);
	let stepsBeforeTarget = $derived(targetIndex >= 0 ? effective.slice(0, targetIndex) : effective);

	let mode = $derived<'paint' | 'mask' | 'crop' | 'preview'>(
		activeTool === 'paint' ? 'paint' : activeTool === 'mask' ? 'mask' : activeTool === 'crop' ? 'crop' : 'preview'
	);

	let cropParams = $derived<CropParams>((pendingHere?.tool === 'crop' ? (pendingHere.params as CropParams) : null) ?? { plan: EMPTY_IMAGE_PLAN, aspect: 'free' });
	let catalogTool = $derived(activeTool && !['crop', 'resize', 'paint', 'mask', 'effects'].includes(activeTool) ? catalog.find((tool) => tool.id === (CATALOG_TOOL[activeTool!] ?? activeTool)) ?? null : null);
	let catalogValues = $derived<Record<string, unknown>>(
		pendingHere && catalogTool ? ((pendingHere.params as CatalogParams).values ?? {}) : catalogTool ? initialValues(catalogTool) : {}
	);
	let effectParams = $derived<EffectParams | null>(pendingHere?.tool === 'effects' ? (pendingHere.params as EffectParams) : null);

	let mainSteps = $derived(mode === 'preview' ? previewSteps(stack) : mode === 'crop' ? (pendingHere?.tool === 'crop' ? stepsBeforeTarget : effective) : effective);
	let beforeList = $derived(mode === 'preview' && compare ? (pending ? beforeSteps(stack) : []) : null);
	let thumbList = $derived(activeTool === 'effects' ? (pending ? beforeSteps(stack) : effective) : null);
	let paintList = $derived(mode === 'paint' ? (pendingHere?.tool === 'paint' ? stepsBeforeTarget : effective) : null);

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
	let undoText = $derived(paintUndo ? (paintUndo.undoLabel ?? 'stroke') : undoLabel(stack));
	let canUndoNow = $derived(!!paintUndo || canUndo(stack));
	let canRedoNow = $derived(mode === 'paint' && paintState?.canRedo ? true : canRedo(stack));
	let redoText = $derived(mode === 'paint' && paintState?.canRedo ? (paintState.redoLabel ?? 'stroke') : redoLabel(stack));
	let canSave = $derived(!!source && !!resolvedRef && !saving && !busy && (effective.length > 0 || !!pending || mode === 'paint'));

	let generationTabs = $derived($tabsStore.tabs.filter(isGenerationTab));
	let tabFormKey = $derived(generationTabs.map((tab) => `${tab.id}|${tab.selectedPreset}|${tab.selectedMode}|${tab.selectedVariant ?? ''}`).join(','));
	let sendTargets = $derived(sendTargetsFor(generationTabs, fieldsByTab, presetNames, !!liveMaskNow, sendFirst));

	let sourceInfo = $derived(
		origin === 'history' ? { label: 'History', icon: 'clock' } : origin === 'library' ? { label: 'Library', icon: 'image' } : origin === 'upload' ? { label: 'Dropped file', icon: 'upload' } : null
	);

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
		if (!pending && compare) compare = false;
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
		if (hint.effect && ADJUST_OPS.includes(hint.effect)) {
			if (activeTool !== 'effects') await selectTool('effects');
			const def = getOp(hint.effect);
			if (!def) return;
			const values = Object.fromEntries(scalarParams(def).map((param) => [param.id, param.default]));
			setDraft('effects', { kind: 'adjust', op: def.id, name: def.label, values } satisfies EffectParams);
		}
	}

	let writeTimer: ReturnType<typeof setTimeout> | null = null;
	let lastWritten = JSON.stringify(writeImageState(initial));
	let pendingWrite: Record<string, unknown> | null = null;

	function flushWrite() {
		if (writeTimer) clearTimeout(writeTimer);
		writeTimer = null;
		if (!pendingWrite) return;
		const next = pendingWrite;
		pendingWrite = null;
		host.setState(next);
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
		const actions = {
			undo: { label: undoText ? `Undo ${undoText}` : 'Undo', disabled: !canUndoNow, run: doUndo },
			redo: { label: redoText ? `Redo ${redoText}` : 'Redo', disabled: !canRedoNow, run: doRedo },
			save: { label: 'Save', disabled: !canSave, run: () => (saveMenuOpen = true) }
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
				.then((schema) => (fieldsByTab = { ...fieldsByTab, [id]: imageFieldsOf(schema) }))
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
						renderError = error instanceof Error ? error.message : 'The preview could not be drawn.';
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

	function showCanvas(node: HTMLCanvasElement, canvas: HTMLCanvasElement | null) {
		function draw(next: HTMLCanvasElement | null) {
			if (!next) return;
			node.width = next.width;
			node.height = next.height;
			node.getContext('2d')?.drawImage(next, 0, 0);
		}
		draw(canvas);
		return { update: draw };
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
		if (tool === 'effects') {
			const effect = params as EffectParams;
			if (effect.kind === 'look') return lookDraft(effect) as StepDraft<unknown>;
			return adjustDraft(effect) as StepDraft<unknown>;
		}
		return catalogDraft(tool, (params as CatalogParams).values, catalog) as StepDraft<unknown>;
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

	async function landPaint(): Promise<boolean> {
		if (!paintStage) return true;
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
			mask = { path: stored.data.path, url: `/api/media/serve?path=${encodeURIComponent(stored.data.path)}`, geometry: geometryKey(effective) };
			maskDirty = false;
			return true;
		} catch (error) {
			notice = describeMediaToolFailure(error);
			return false;
		} finally {
			busy = false;
		}
	}

	async function leaveTool(): Promise<boolean> {
		if (mode === 'paint') return landPaint();
		if (mode === 'mask') return landMask();
		if (pending) stack = apply(stack);
		return true;
	}

	async function selectTool(id: string) {
		if (!source || busy) return;
		if (id === activeTool && !(pending && pending.tool !== id)) return;
		if (!(await leaveTool())) return;
		notice = null;
		compare = false;
		activeTool = id;
		host.setActiveTool(id);
	}

	async function reopenStep(id: string) {
		const step = stack.steps.find((candidate) => candidate.id === id);
		if (!step || busy) return;
		if (pending?.target === id) return;
		if (!(await leaveTool())) return;
		stack = reopen(stack, id);
		activeTool = step.tool;
		host.setActiveTool(step.tool);
	}

	function doUndo() {
		if (paintUndo && paintSession) {
			paintSession.undo();
			return;
		}
		stack = undo(stack);
	}

	function doRedo() {
		if (mode === 'paint' && paintState?.canRedo && paintSession) {
			paintSession.redo();
			return;
		}
		stack = redo(stack);
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
		if (stack.pending) stack = apply(stack);
	}

	function resetTool() {
		notice = null;
		if (mode === 'mask') {
			maskLayer?.clear();
			mask = null;
			maskDirty = false;
			return;
		}
		if (mode === 'paint') {
			paintKey += 1;
			if (pending?.tool === 'paint' && !pending.target) stack = discardPending(stack);
			return;
		}
		if (pending) stack = discardPending(stack);
	}

	let paintKey = $state(0);

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
		const steps = appliedSteps(stack);
		if (!steps.some((step) => !step.hidden)) {
			notice = 'There are no visible edits to save.';
			return;
		}
		saving = { percent: 0, label: 'Saving' };
		let stop: (() => void) | null = null;
		try {
			const payload = await renderer.payload(steps);
			const handle = await mediaToolRuns.start({
				keys: [`tool-tab:${host.tabId}`],
				toolId: payload.find((step) => !step.hidden)?.tool ?? 'edit',
				label: 'Image edit',
				body: runBody(payload, ref, saveMode, saved?.itemId ?? null)
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

	async function renderFull(): Promise<Rendered> {
		if (!sourceSize || !sourceUrl) throw new Error('Open a picture first');
		renderer.setSource({ url: sourceUrl, size: sourceSize, ref: resolvedRef });
		return renderer.render(effectiveSteps(stack), { maxSide: FULL_SIDE });
	}

	async function sendTo(targetId: string) {
		const target = sendTargets.find((entry) => entry.id === targetId);
		if (!target) return;
		if (!(await prepareSave())) return;
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
		void runSave(saved ? 'update' : 'new');
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

	function onPaste(event: ClipboardEvent) {
		if (source) return;
		const file = pastedImage(event.clipboardData?.items);
		if (!file) return;
		event.preventDefault();
		void openFile(file);
	}

	let optionsTitle = $derived(activeTool ? toolMeta(activeTool, catalog).label : 'Tools');
	let optionsIcon = $derived(activeTool ? toolMeta(activeTool, catalog).icon : 'sliders');
	let applyLabel = $derived(
		activeTool === 'crop' ? 'Apply crop' : activeTool === 'effects' ? (effectParams?.kind === 'look' ? 'Apply look' : 'Apply') : activeTool === 'mask' ? 'Save mask' : activeTool === 'paint' ? 'Apply paint' : 'Apply'
	);
	let canApply = $derived(
		!busy &&
			(mode === 'paint'
				? !!paintState?.ready
				: mode === 'mask'
					? maskDirty
					: !!pendingHere || !!catalogTool)
	);
	let pendingTools = $derived([...(pending ? [pending.tool] : []), ...(mode === 'paint' && paintState?.dirty ? ['paint'] : []), ...(maskDirty ? ['mask'] : [])]);
	let originalSummary = $derived(sourceSize ? `${sourceSize.width}×${sourceSize.height} · ${extension}` : extension);
	let stackMore = $derived(applied.length ? [{ id: 'clear', label: 'Remove all edits', icon: 'trash' }] : []);
</script>

<svelte:window
	onpaste={onPaste}
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
	{#if activeTool === 'crop' && cropInput}
		<CropOptions params={cropParams} input={cropInput} {phone} onchange={(next) => setDraft('crop', next)} />
	{:else if activeTool === 'resize' && resizeParams && resizeInput}
		<ResizeOptions params={resizeParams} input={resizeInput} {phone} onchange={(next) => setDraft('resize', next)} />
	{:else if activeTool === 'effects'}
		<EffectsOptions params={effectParams} base={thumbRender?.canvas ?? null} {phone} onchange={(next) => setDraft('effects', next)} />
		{#if bakeIsCapped(outputDims) && !phone}
			<p class="text-sm text-warning" data-bake-cap>Looks on pictures over {FULL_SIDE} px are drawn at {FULL_SIDE} px on the long side and scaled back up when saved.</p>
		{/if}
	{:else if activeTool === 'paint'}
		{#if paintSession && paintState}
			<PaintOptions session={paintSession} state={paintState} {phone} />
		{:else}
			<div class="flex justify-center py-4"><Spinner /></div>
		{/if}
	{:else if activeTool === 'mask'}
		<SegmentedControl
			variant="toggle"
			fullWidth
			ariaLabel="Mask brush"
			items={[
				{ id: 'paint', label: 'Paint', icon: 'brush' },
				{ id: 'erase', label: 'Erase', icon: 'editor-eye-off' }
			]}
			selected={maskErase ? 'erase' : 'paint'}
			onSelect={(id) => (maskErase = id === 'erase')}
		/>
		<SliderField name="brush" config={{ title: 'Brush size', min: 4, max: 300, step: 1, default: 48, unit: 'px' }} value={maskBrush} onChange={(_name, value) => (maskBrush = Number(value))} />
		{#if staleMask}
			<p class="text-sm text-warning">The mask was painted before a shape change, so it no longer lines up. Paint it again.</p>
		{/if}
		{#if !phone}
			<p class="text-sm text-fg-muted">The mask is not an edit step. It travels with Send to a generation as the field's inpaint mask.</p>
		{/if}
	{:else if catalogTool || (activeTool && !isCoreTool(activeTool))}
		<CatalogToolOptions tool={catalogTool} values={catalogValues} note={activeTool === 'subject' ? 'Needs a picture with transparency, for example after Color key.' : null} onchange={(next) => setDraft(activeTool!, { values: next })} />
	{:else if !activeTool}
		<p class="text-sm text-fg-muted">Pick a tool on the left.</p>
	{:else}
		<div class="flex justify-center py-4"><Spinner /></div>
	{/if}
{/snippet}

{#snippet canvasArea()}
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
		<EditorCanvas mediaWidth={shownSize.width} mediaHeight={shownSize.height} bind:viewport {compare} {phone}>
			{#snippet layer()}
				<canvas use:showCanvas={mode === 'crop' ? cropCanvas : (mainRender?.canvas ?? null)} class="block h-full w-full" data-editor-layer aria-label="Edited picture"></canvas>
			{/snippet}
			{#snippet before()}
				{#if beforeRender}
					<canvas use:showCanvas={beforeRender.canvas} class="block h-full w-full" data-editor-before aria-hidden="true"></canvas>
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
				{:else if mode === 'mask' && mainRender}
					{#key `${mask?.path ?? ''}:${mainRender.canvas.width}x${mainRender.canvas.height}`}
						<MaskLayer
							bind:this={maskLayer}
							width={mainRender.canvas.width}
							height={mainRender.canvas.height}
							existing={liveMaskNow?.url ?? null}
							brush={maskBrush}
							erase={maskErase}
							onstroke={(painted) => {
								if (painted) maskDirty = true;
							}}
						/>
					{/key}
				{/if}
			{/snippet}
			{#snippet badge()}
				{#if rendering && !phone}
					<CanvasChip><span class="inline-flex items-center gap-1.5"><Spinner size="sm" />Preview</span></CanvasChip>
				{/if}
			{/snippet}
		</EditorCanvas>
		{#if renderError}
			<div class="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
				<span class="rounded bg-danger-solid/90 px-3 py-1 text-sm text-white" role="alert" data-render-error>{renderError}</span>
			</div>
		{/if}
	{:else}
		<div class="flex flex-1 items-center justify-center bg-scrim"><Spinner /></div>
	{/if}
{/snippet}

{#snippet stackView(variant: 'panel' | 'sheet')}
	<EditStack
		{stack}
		original={{ summary: originalSummary, thumb: sourceUrl }}
		{variant}
		pinned={IMAGE_PINNED_LANES}
		moreItems={stackMore}
		onreopen={(id) => void reopenStep(id)}
		onseek={(cursor) => (stack = seek(stack, cursor))}
		ontogglehidden={(id) => (stack = toggleHidden(stack, id))}
		onremove={(id) => (stack = remove(stack, id))}
		onmove={(id, to) => (stack = move(stack, id, to, IMAGE_PINNED_LANES))}
		onundo={doUndo}
		onredo={doRedo}
		onmore={(id) => {
			if (id === 'clear') stack = createStack();
		}}
	/>
{/snippet}

{#snippet saveRows(pick: (action: SaveAction) => void)}
	<SaveMenuRows saved={!!saved} {replace} {sendTargets} downloadFormat="PNG" copyLabel="Copy image" onchoose={pick} />
{/snippet}

<div class="image-editor-tab flex h-full min-h-0 w-full flex-col" data-image-editor data-tool={activeTool ?? ''} data-mode={mode}>
	{#if !source}
		{#if phone}
			<ImageEmptyState phone busy={emptyBusy} error={emptyError} onpick={openSource} onfile={(file) => void openFile(file)} />
		{:else}
			<EditorToolbar
				fileName={null}
				canUndo={false}
				canRedo={false}
				canCompare={false}
				canSave={false}
				replace={{ allowed: false }}
				onundo={() => undefined}
				onredo={() => undefined}
				oncompare={() => undefined}
				onsave={() => undefined}
			/>
			<div class="flex min-h-0 flex-1">
				<ToolRail groups={railGroups} onselect={() => undefined} />
				<ImageEmptyState busy={emptyBusy} error={emptyError} onpick={openSource} onfile={(file) => void openFile(file)} />
			</div>
		{/if}
	{:else}
		<EditorFrame
			{phone}
			bind:compare
			bind:stackOpen
			canCompare={mode === 'preview' && !!pending}
			{editCount}
			onundo={doUndo}
			onredo={doRedo}
			onsave={primarySave}
			onapply={() => void applyTool()}
			stack={stackView}
		>
			{#snippet toolbar()}
				{#if phone}
					{#if saveMenuOpen}
						<FrameSheet label="Save" onclose={() => (saveMenuOpen = false)}>
							{#snippet header()}
								<span class="text-lg font-semibold text-fg">Save</span>
								<span class="flex-1"></span>
								<span class="truncate font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{fileName}</span>
							{/snippet}
							<div role="menu" aria-label="Save" class="flex flex-col">
								{@render saveRows(onSave)}
							</div>
						</FrameSheet>
					{/if}
				{:else}
					<EditorToolbar
						{fileName}
						chips={outputDims ? [`${outputDims.width}×${outputDims.height}`] : []}
						source={sourceInfo}
						canUndo={canUndoNow}
						canRedo={canRedoNow}
						undoLabel={undoText}
						redoLabel={redoText}
						{compare}
						canCompare={mode === 'preview' && !!pending}
						{canSave}
						saved={!!saved}
						{replace}
						{sendTargets}
						downloadFormat="PNG"
						{saving}
						bind:saveMenuOpen
						onundo={doUndo}
						onredo={doRedo}
						oncompare={(next) => (compare = next)}
						onsave={onSave}
					/>
				{/if}
			{/snippet}
			{#snippet rail()}
				<ToolRail groups={railGroups} active={activeTool} pending={pendingTools} variant={phone ? 'strip' : 'rail'} onselect={(id) => void selectTool(id)} />
			{/snippet}
			{#snippet canvas()}
				{@render canvasArea()}
			{/snippet}
			{#snippet options()}
				<OptionsPanel
					title={optionsTitle}
					icon={optionsIcon}
					{applyLabel}
					{canApply}
					canReset={!busy && (!!pendingHere || mode === 'mask' || mode === 'paint')}
					{phone}
					onapply={() => void applyTool()}
					onreset={resetTool}
				>
					{@render optionsBody()}
				</OptionsPanel>
			{/snippet}
		</EditorFrame>
	{/if}
</div>
