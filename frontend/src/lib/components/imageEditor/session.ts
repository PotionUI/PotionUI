import { loadImage } from '$lib/media/editors/loadImage';
import { captureDoc, restoreDoc, structuralBytes } from './docState';
import {
	flipGeometry,
	identityGeometry,
	isIdentityGeometry,
	rotateGeometry,
	scaleGeometry,
	translateGeometry
} from './docGeometry';
import { canvasToPngFile, flattenDocument } from './flatten';
import { runFilters, type FilterStep } from './adjustments/run';
import { defaultFilterValues } from './adjustments/builtin';
import { renderRecipe } from '$lib/filters/render';
import { makeThumbSource } from '$lib/filters/thumbs';
import type { FilterStepSpec } from '$lib/filters/engine';
import type { ActiveFilter } from '$lib/filters/types';
import {
	canApply,
	historyLabel,
	initialToolState,
	selectActive,
	withCompare,
	withIntensity,
	type FilterToolState
} from '$lib/filters/toolState';
import { toggleStep, setStepParam } from '$lib/filters/steps';
import {
	FIT_MAX_ZOOM,
	MAX_SIDE,
	clampRectToBounds,
	clampSide,
	fitInside,
	fitView,
	fitWithin,
	flipPosition,
	panBy,
	pixelRect,
	rotatePosition,
	screenToDoc,
	zoomAbout
} from './geometry';
import { History, type Command } from './history';
import { DEFAULT_PEN, DEFAULT_SIZE, rgbaToHex } from './palette';
import { blendByMask, clearByMask, extractByMask, sampleMaskForLayer } from './raster/layerMask';
import { buildSelection, rectPath } from './raster/selection';
import { createPatch, patchBytes, writePatch } from './patch';
import { paintFilters, pluginTools, setActiveEditor } from './registries';
import { readTheme, type EditorTheme } from './theme';
import { createBrushTool, createEraserTool } from './tools/brush';
import { createAdjustTool } from './tools/adjust';
import { createCropTool } from './tools/crop';
import { createFillTool } from './tools/fill';
import { createFiltersTool } from './tools/filters';
import { createMoveTool } from './tools/move';
import { createPickTool } from './tools/pick';
import { createLassoTool, createRectSelectTool } from './tools/select';
import { createTransformTool } from './tools/transform';
import type {
	DocGeometry,
	DocSnapshot,
	EditorApi,
	FilterValues,
	Layer,
	PaintDocument,
	PaintFilter,
	PaintTool,
	Point,
	Rect,
	Selection,
	ToolHost,
	ToolPointer,
	ToolSettings,
	View
} from './types';

export interface FilterSnapshot {
	active: ActiveFilter | null;
	intensity: number;
	compare: boolean;
	fineTune: boolean;
	steps: FilterStepSpec[];
	error: string | null;
}

export interface SessionSnapshot {
	filter: FilterSnapshot;
	contentRevision: number;
	toolId: string;
	tools: PaintTool[];
	settings: ToolSettings;
	zoom: number;
	view: View;
	canUndo: boolean;
	canRedo: boolean;
	undoLabel: string | null;
	redoLabel: string | null;
	dirty: boolean;
	width: number;
	height: number;
	geometryChanged: boolean;
	sourceReplaced: boolean;
	sourceName: string | null;
	notice: string | null;
	ready: boolean;
	layers: Layer[];
	activeIndex: number;
	revision: number;
	hasSelection: boolean;
	selectionSize: { width: number; height: number } | null;
	hasClipboard: boolean;
	cropRect: Rect | null;
	adjustValues: Record<string, FilterValues>;
	adjustActive: boolean;
	filters: PaintFilter[];
	spaceHeld: boolean;
	panning: boolean;
}

export interface BlankOptions {
	width: number;
	height: number;
	background: 'white' | 'black' | 'transparent';
	pen: string | null;
}

const PREVIEW_LONG_EDGE = 1600;
const DESKTOP_BUDGET = 256 * 1024 * 1024;
const PHONE_BUDGET = 96 * 1024 * 1024;

function historyBudget(): number {
	if (typeof matchMedia === 'function' && matchMedia('(max-width: 767px)').matches) {
		return PHONE_BUDGET;
	}
	return DESKTOP_BUDGET;
}

function makeCanvas(width: number, height: number): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = Math.max(1, Math.round(width));
	canvas.height = Math.max(1, Math.round(height));
	return canvas;
}

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
	const context = canvas.getContext('2d');
	if (!context) throw new Error('This browser could not open a drawing surface.');
	return context;
}

export class PaintSession implements ToolHost, EditorApi {
	readonly history = new History(historyBudget());
	doc: PaintDocument = { width: 0, height: 0, layers: [], activeIndex: 0 };
	view: View = { zoom: 1, panX: 0, panY: 0 };
	settings: ToolSettings = {
		color: DEFAULT_PEN,
		size: DEFAULT_SIZE,
		opacity: 100,
		tolerance: 32
	};
	toolId = 'brush';
	cursor: Point | null = null;
	selection: Selection | null = null;
	cropRect: Rect | null = null;
	geometry: DocGeometry = identityGeometry(0, 0);
	sourceReplaced = false;
	sourceName: string | null = null;
	notice: string | null = null;
	ready = false;

	private builtins: PaintTool[] = [];
	private stage: HTMLCanvasElement | null = null;
	private context: CanvasRenderingContext2D | null = null;
	private stageWidth = 0;
	private stageHeight = 0;
	private dpr = 1;
	private autoFit = true;
	private theme: EditorTheme | null = null;
	private nextLayerId = 1;
	private frame = 0;
	private spaceHeld = false;
	private pointers = new Map<number, Point>();
	private activePointerId: number | null = null;
	private panning: { x: number; y: number; view: View } | null = null;
	private pinch: { distance: number; view: View; center: Point } | null = null;
	private preview: { layer: Layer; canvas: HTMLCanvasElement; alpha: number } | null = null;
	private listeners = new Set<() => void>();
	private pattern: CanvasPattern | null = null;
	private draft: Point[] | null = null;
	private clipboard: { canvas: HTMLCanvasElement; x: number; y: number } | null = null;
	private groupDepth = 0;
	private groupCommands: Command[] = [];
	private revision = 0;
	private ants = 0;
	private antsTimer: ReturnType<typeof setInterval> | null = null;
	private maskCache = new WeakMap<Selection, Map<number, Uint8ClampedArray>>();
	private adjustValues: Record<string, FilterValues> = {};
	private adjustPreview: { layer: Layer; canvas: HTMLCanvasElement } | null = null;
	private adjustToken = 0;
	private transformBefore: DocSnapshot | null = null;
	private adjustBase: { layer: Layer; canvas: HTMLCanvasElement; image: ImageData } | null = null;
	private filterState: FilterToolState<ActiveFilter> = initialToolState<ActiveFilter>();
	private filterSteps: FilterStepSpec[] = [];
	private fineTune = false;
	private filterError: string | null = null;
	private filterPreview: { layer: Layer; canvas: HTMLCanvasElement } | null = null;
	private filterToken = 0;
	private filterBase: {
		layer: Layer;
		canvas: HTMLCanvasElement;
		scale: number;
		image: ImageData;
	} | null = null;
	private contentRevision = 0;
	private unsubscribers: Array<() => void> = [];

	constructor() {
		this.builtins = [
			createMoveTool(),
			createTransformTool(),
			createBrushTool(),
			createEraserTool(),
			createFillTool(),
			createPickTool(),
			createRectSelectTool(),
			createLassoTool(),
			createCropTool(),
			createAdjustTool(),
			createFiltersTool()
		];
		this.unsubscribers.push(
			this.history.subscribe(() => {
				this.contentRevision++;
				this.emit();
			}),
			pluginTools.subscribe(() => this.emit()),
			paintFilters.subscribe(() => {
				this.ensureAdjustValues();
				this.emit();
			})
		);
		this.ensureAdjustValues();
		setActiveEditor(this);
	}

	get allTools(): PaintTool[] {
		return [...this.builtins, ...pluginTools.list()];
	}

	getTool(id: string): PaintTool | undefined {
		return this.allTools.find((tool) => tool.id === id);
	}

	get tool(): PaintTool | undefined {
		return this.getTool(this.toolId);
	}

	get geometryChanged(): boolean {
		return !isIdentityGeometry(this.geometry);
	}

	get activeLayer(): Layer | null {
		return this.doc.layers[this.doc.activeIndex] ?? null;
	}

	get zoom(): number {
		return this.view.zoom;
	}

	get docWidth(): number {
		return this.doc.width;
	}

	get docHeight(): number {
		return this.doc.height;
	}

	get cursorColor(): string {
		return this.theme?.fg ?? 'rgb(255 255 255)';
	}

	attach(stage: HTMLCanvasElement): void {
		this.stage = stage;
		this.context = stage.getContext('2d');
		this.theme = readTheme();
		this.pattern = null;
		this.invalidate();
	}

	detach(): void {
		if (this.frame) cancelAnimationFrame(this.frame);
		this.frame = 0;
		this.stopAnts();
		this.stage = null;
		this.context = null;
		this.listeners.clear();
		for (const off of this.unsubscribers) off();
		this.unsubscribers = [];
		setActiveEditor(null);
	}

	refreshTheme(): void {
		this.theme = readTheme();
		this.pattern = null;
		this.invalidate();
	}

	resize(width: number, height: number, dpr: number): void {
		this.stageWidth = width;
		this.stageHeight = height;
		this.dpr = dpr;
		if (this.stage) {
			this.stage.width = Math.max(1, Math.round(width * dpr));
			this.stage.height = Math.max(1, Math.round(height * dpr));
		}
		if (this.autoFit && this.ready) this.fit();
		else this.invalidate();
	}

	subscribe(listener: () => void): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	snapshot(): SessionSnapshot {
		const adjustActive = this.adjustSteps().some((step) => step.filter.active(step.values));
		return {
			filter: {
				active: this.filterState.active,
				intensity: this.filterState.intensity,
				compare: this.filterState.compare,
				fineTune: this.fineTune,
				steps: this.filterSteps,
				error: this.filterError
			},
			contentRevision: this.contentRevision,
			toolId: this.toolId,
			tools: this.allTools,
			settings: { ...this.settings },
			zoom: this.view.zoom,
			view: { ...this.view },
			canUndo: this.history.canUndo,
			canRedo: this.history.canRedo,
			undoLabel: this.history.undoLabel,
			redoLabel: this.history.redoLabel,
			dirty: this.history.dirty,
			width: this.doc.width,
			height: this.doc.height,
			geometryChanged: this.geometryChanged,
			sourceReplaced: this.sourceReplaced,
			sourceName: this.sourceName,
			notice: this.notice,
			ready: this.ready,
			layers: [...this.doc.layers],
			activeIndex: this.doc.activeIndex,
			revision: this.revision,
			hasSelection: this.selection !== null,
			selectionSize: this.selection
				? { width: this.selection.bounds.width, height: this.selection.bounds.height }
				: null,
			hasClipboard: this.clipboard !== null,
			cropRect: this.cropRect,
			adjustValues: this.adjustValues,
			adjustActive,
			filters: paintFilters.list(),
			spaceHeld: this.spaceHeld,
			panning: this.panning !== null
		};
	}

	private emit(): void {
		this.revision++;
		for (const listener of this.listeners) listener();
	}

	private resetState(): void {
		this.history.clear();
		this.selection = null;
		this.draft = null;
		this.cropRect = null;
		this.groupDepth = 0;
		this.groupCommands = [];
		this.preview = null;
		this.adjustPreview = null;
		this.dropBases();
		this.resetAdjust(false);
		this.stopAnts();
	}

	private newLayer(name: string, width: number, height: number, x = 0, y = 0): Layer {
		return {
			id: this.nextLayerId++,
			name,
			canvas: makeCanvas(width, height),
			x,
			y,
			visible: true,
			opacity: 1
		};
	}

	async openSource(url: string, name: string | null): Promise<void> {
		const image = await loadImage(url);
		const canvas = makeCanvas(image.naturalWidth, image.naturalHeight);
		context2d(canvas).drawImage(image, 0, 0);
		this.sourceName = name;
		this.loadCanvas(canvas, false);
	}

	private loadCanvas(source: HTMLCanvasElement, replaced: boolean): void {
		const fitted = fitWithin(source.width, source.height, MAX_SIDE);
		const layer = this.newLayer('Background', fitted.width, fitted.height);
		const context = context2d(layer.canvas);
		context.imageSmoothingQuality = 'high';
		context.drawImage(source, 0, 0, fitted.width, fitted.height);
		this.resetState();
		this.doc = { width: fitted.width, height: fitted.height, layers: [layer], activeIndex: 0 };
		this.geometry = identityGeometry(this.doc.width, this.doc.height);
		this.sourceReplaced = replaced;
		this.notice =
			fitted.scale < 1
				? `This image was larger than ${MAX_SIDE} px and was scaled down to ${fitted.width} x ${fitted.height}.`
				: null;
		this.ready = true;
		this.autoFit = true;
		this.fit();
		this.emit();
	}

	openBlank(options: BlankOptions): void {
		const width = clampSide(options.width);
		const height = clampSide(options.height);
		const layer = this.newLayer('Background', width, height);
		const context = context2d(layer.canvas);
		if (options.background !== 'transparent') {
			context.fillStyle = options.background === 'black' ? 'rgb(0, 0, 0)' : 'rgb(255, 255, 255)';
			context.fillRect(0, 0, width, height);
		}
		this.resetState();
		this.doc = { width, height, layers: [layer], activeIndex: 0 };
		this.sourceName = null;
		this.geometry = identityGeometry(this.doc.width, this.doc.height);
		this.sourceReplaced = false;
		this.notice = null;
		if (options.pen) this.settings = { ...this.settings, color: options.pen };
		this.ready = true;
		this.autoFit = true;
		this.fit();
		this.emit();
	}

	openImage(canvas: HTMLCanvasElement, name: string): void {
		this.sourceName = name;
		this.loadCanvas(canvas, true);
	}

	addImage(canvas: HTMLCanvasElement, name: string): void {
		if (!this.ready) return;
		const fitted = fitInside(canvas, this.doc, 0.6);
		const piece = makeCanvas(fitted.width, fitted.height);
		const context = context2d(piece);
		context.imageSmoothingQuality = 'high';
		context.drawImage(canvas, 0, 0, piece.width, piece.height);
		const x = Math.round((this.doc.width - piece.width) / 2);
		const y = Math.round((this.doc.height - piece.height) / 2);
		this.selection = null;
		const layer = this.addPieceLayer(piece, x, y, name.replace(/\.[^.]+$/, '') || 'Image');
		layer.source = canvas;
		this.setTool('transform');
		this.invalidate();
	}

	setTool(id: string): void {
		if (!this.getTool(id) || id === this.toolId) return;
		if (this.activePointerId !== null) this.tool?.pointerCancel?.(this);
		if (this.toolId === 'adjust' && !this.fineTune) this.discardAdjust();
		if (this.toolId === 'filters' || this.fineTune) this.discardFilter();
		if (this.toolId === 'crop') this.cropRect = null;
		this.toolId = id;
		if (id === 'adjust') this.scheduleAdjust();
		this.emit();
		this.invalidate();
	}

	setSetting<K extends keyof ToolSettings>(key: K, value: ToolSettings[K]): void {
		this.settings = { ...this.settings, [key]: value };
		this.emit();
		this.invalidate();
	}

	setColor(color: string): void {
		this.setSetting('color', color);
	}

	adjustSize(delta: number): void {
		this.setSetting('size', Math.min(200, Math.max(1, this.settings.size + delta)));
	}

	undo(): void {
		this.history.undo();
		this.afterHistory();
	}

	redo(): void {
		this.history.redo();
		this.afterHistory();
	}

	private afterHistory(): void {
		this.dropBases();
		if (this.toolId === 'adjust') this.scheduleAdjust();
		this.startAntsIfNeeded();
		this.invalidate();
		this.emit();
	}

	markSaved(): void {
		this.history.markSaved();
	}

	fit(): void {
		if (!this.doc.width || !this.stageWidth) return;
		this.view = fitView(
			{ width: this.stageWidth, height: this.stageHeight },
			{ width: this.doc.width, height: this.doc.height },
			24,
			FIT_MAX_ZOOM
		);
		this.autoFit = true;
		this.emit();
		this.invalidate();
	}

	zoomBy(factor: number, anchor?: Point): void {
		const at = anchor ?? { x: this.stageWidth / 2, y: this.stageHeight / 2 };
		this.view = zoomAbout(this.view, factor, at);
		this.autoFit = false;
		this.emit();
		this.invalidate();
	}

	setSpaceHeld(held: boolean): void {
		if (this.spaceHeld === held) return;
		this.spaceHeld = held;
		this.emit();
		this.invalidate();
	}

	invalidate(): void {
		if (this.frame || !this.stage) return;
		this.frame = requestAnimationFrame(() => {
			this.frame = 0;
			this.render();
		});
	}

	snapshotLayer(layer: Layer): HTMLCanvasElement {
		const copy = makeCanvas(layer.canvas.width, layer.canvas.height);
		context2d(copy).drawImage(layer.canvas, 0, 0);
		return copy;
	}

	setStrokePreview(layer: Layer | null, canvas: HTMLCanvasElement | null, alpha: number): void {
		this.preview = layer && canvas ? { layer, canvas, alpha } : null;
	}

	private pushCommand(command: Command): void {
		if (this.groupDepth > 0) this.groupCommands.push(command);
		else this.history.push(command);
	}

	beginGroup(): void {
		if (this.groupDepth === 0) this.groupCommands = [];
		this.groupDepth++;
	}

	endGroup(label: string, discard = false): void {
		if (this.groupDepth === 0) return;
		this.groupDepth--;
		if (this.groupDepth > 0) return;
		const commands = this.groupCommands;
		this.groupCommands = [];
		if (discard) {
			for (const command of [...commands].reverse()) command.undo();
			this.dropBases();
			this.invalidate();
			this.emit();
			return;
		}
		if (commands.length === 0) return;
		this.history.push({
			label,
			bytes: commands.reduce((sum, command) => sum + command.bytes, 0),
			undo: () => {
				for (const command of [...commands].reverse()) command.undo();
			},
			redo: () => {
				for (const command of commands) command.redo();
			}
		});
	}

	commitPixels(label: string, layer: Layer, dirty: Rect, before: HTMLCanvasElement): void {
		const rect = pixelRect(dirty, layer.canvas);
		if (!rect) return;
		const beforeContext = before.getContext('2d');
		const afterContext = layer.canvas.getContext('2d');
		if (!beforeContext || !afterContext) return;
		const beforeData = beforeContext.getImageData(rect.x, rect.y, rect.width, rect.height);
		const afterData = afterContext.getImageData(rect.x, rect.y, rect.width, rect.height);
		const patch = createPatch(rect, beforeData.data, afterData.data);
		layer.source = undefined;
		if (!patch) return;

		const apply = (side: 'before' | 'after') => {
			const context = layer.canvas.getContext('2d');
			if (!context) return;
			const region = context.getImageData(patch.x, patch.y, patch.width, patch.height);
			writePatch(
				{ width: region.width, height: region.height, data: region.data },
				{ ...patch, x: 0, y: 0 },
				side
			);
			context.putImageData(region, patch.x, patch.y);
		};

		this.pushCommand({
			label,
			bytes: patchBytes(patch),
			undo: () => apply('before'),
			redo: () => apply('after')
		});
		this.dropBases();
		this.invalidate();
	}

	captureDoc(): DocSnapshot {
		return captureDoc(this.doc, this.selection, this.geometry);
	}

	commitStructure(label: string, before: DocSnapshot): void {
		const after = this.captureDoc();
		const apply = (snapshot: DocSnapshot) => {
			const restored = restoreDoc(this.doc, snapshot);
			this.selection = restored.selection;
			this.geometry = restored.geometry;
			this.dropBases();
			this.startAntsIfNeeded();
			this.invalidate();
		};
		this.pushCommand({
			label,
			bytes: structuralBytes(before, after),
			undo: () => apply(before),
			redo: () => apply(after)
		});
		this.invalidate();
		this.emit();
	}

	private mutate(label: string, change: () => void): void {
		const before = this.captureDoc();
		change();
		this.commitStructure(label, before);
	}

	addPieceLayer(canvas: HTMLCanvasElement, x: number, y: number, name: string): Layer {
		const layer = this.newLayer(this.uniqueName(name), canvas.width, canvas.height, x, y);
		context2d(layer.canvas).drawImage(canvas, 0, 0);
		this.mutate('Add layer', () => {
			this.doc.layers.splice(this.doc.activeIndex + 1, 0, layer);
			this.doc.activeIndex += 1;
		});
		return layer;
	}

	private uniqueName(base: string): string {
		const taken = new Set(this.doc.layers.map((layer) => layer.name));
		if (!taken.has(base)) return base;
		let n = 2;
		while (taken.has(`${base} ${n}`)) n++;
		return `${base} ${n}`;
	}

	ensureLayerSource(layer: Layer): HTMLCanvasElement {
		if (!layer.source) layer.source = this.snapshotLayer(layer);
		return layer.source;
	}

	replaceLayerCanvas(layer: Layer, canvas: HTMLCanvasElement, x: number, y: number): void {
		layer.canvas = canvas;
		layer.x = x;
		layer.y = y;
		this.dropBases();
	}

	setCropRect(rect: Rect | null): void {
		this.cropRect = rect;
		this.emit();
		this.invalidate();
	}

	applyCrop(): void {
		const rect = this.cropRect
			? clampRectToBounds(this.cropRect, { width: this.doc.width, height: this.doc.height })
			: null;
		if (!rect) return;
		this.mutate('Crop canvas', () => {
			for (const layer of this.doc.layers) {
				layer.x -= rect.x;
				layer.y -= rect.y;
			}
			this.geometry = translateGeometry(this.geometry, -rect.x, -rect.y, rect.width, rect.height);
			this.doc.width = rect.width;
			this.doc.height = rect.height;
			this.selection = null;
			this.cropRect = null;
		});
		this.stopAnts();
		this.fit();
	}

	cancelCrop(): void {
		this.cropRect = null;
		this.emit();
		this.invalidate();
	}

	pickColor(point: Point): void {
		const x = Math.floor(point.x);
		const y = Math.floor(point.y);
		if (x < 0 || y < 0 || x >= this.doc.width || y >= this.doc.height) return;
		const pixel = context2d(flattenDocument(this.doc)).getImageData(x, y, 1, 1).data;
		this.setColor(rgbaToHex(pixel[0], pixel[1], pixel[2]));
	}

	flatten(): HTMLCanvasElement {
		return flattenDocument(this.doc);
	}

	async exportFile(fileName: string): Promise<File> {
		return canvasToPngFile(this.flatten(), fileName);
	}

	setActiveLayer(index: number): void {
		if (index < 0 || index >= this.doc.layers.length) return;
		if (index === this.doc.activeIndex) return;
		this.doc.activeIndex = index;
		this.dropBases();
		if (this.toolId === 'adjust') this.scheduleAdjust();
		this.emit();
		this.invalidate();
	}

	addLayer(): void {
		const layer = this.newLayer(this.uniqueName('Layer'), this.doc.width, this.doc.height);
		this.mutate('Add layer', () => {
			this.doc.layers.splice(this.doc.activeIndex + 1, 0, layer);
			this.doc.activeIndex += 1;
		});
	}

	duplicateLayer(): void {
		const source = this.activeLayer;
		if (!source) return;
		const layer = this.newLayer(
			this.uniqueName(`${source.name} copy`),
			source.canvas.width,
			source.canvas.height,
			source.x,
			source.y
		);
		context2d(layer.canvas).drawImage(source.canvas, 0, 0);
		layer.opacity = source.opacity;
		this.mutate('Duplicate layer', () => {
			this.doc.layers.splice(this.doc.activeIndex + 1, 0, layer);
			this.doc.activeIndex += 1;
		});
	}

	deleteLayer(): void {
		if (this.doc.layers.length < 2) return;
		this.mutate('Delete layer', () => {
			this.doc.layers.splice(this.doc.activeIndex, 1);
			this.doc.activeIndex = Math.max(0, this.doc.activeIndex - 1);
		});
	}

	mergeDown(): void {
		const index = this.doc.activeIndex;
		if (index < 1) return;
		const top = this.doc.layers[index];
		const bottom = this.doc.layers[index - 1];
		const x0 = Math.min(bottom.x, top.x);
		const y0 = Math.min(bottom.y, top.y);
		const x1 = Math.max(bottom.x + bottom.canvas.width, top.x + top.canvas.width);
		const y1 = Math.max(bottom.y + bottom.canvas.height, top.y + top.canvas.height);
		const merged = makeCanvas(x1 - x0, y1 - y0);
		const context = context2d(merged);
		context.globalAlpha = bottom.opacity;
		context.drawImage(bottom.canvas, bottom.x - x0, bottom.y - y0);
		context.globalAlpha = top.visible ? top.opacity : 0;
		context.drawImage(top.canvas, top.x - x0, top.y - y0);
		this.mutate('Merge down', () => {
			bottom.canvas = merged;
			bottom.x = x0;
			bottom.y = y0;
			bottom.opacity = 1;
			bottom.source = undefined;
			this.doc.layers.splice(index, 1);
			this.doc.activeIndex = index - 1;
		});
	}

	moveLayer(direction: 'up' | 'down'): void {
		const index = this.doc.activeIndex;
		const target = direction === 'up' ? index + 1 : index - 1;
		if (target < 0 || target >= this.doc.layers.length) return;
		this.mutate('Reorder layer', () => {
			const [layer] = this.doc.layers.splice(index, 1);
			this.doc.layers.splice(target, 0, layer);
			this.doc.activeIndex = target;
		});
	}

	toggleLayerVisible(index: number): void {
		const layer = this.doc.layers[index];
		if (!layer) return;
		this.mutate('Toggle layer', () => {
			layer.visible = !layer.visible;
		});
	}

	setLayerOpacity(index: number, opacity: number): void {
		const layer = this.doc.layers[index];
		if (!layer) return;
		layer.opacity = opacity;
		this.invalidate();
		this.emit();
	}

	scaleActiveLayer(percent: number, commit: boolean): void {
		const layer = this.activeLayer;
		if (!layer) return;
		if (!this.transformBefore) this.transformBefore = this.captureDoc();
		const source = this.ensureLayerSource(layer);
		const centerX = layer.x + layer.canvas.width / 2;
		const centerY = layer.y + layer.canvas.height / 2;
		const width = Math.max(1, Math.round((source.width * percent) / 100));
		const height = Math.max(1, Math.round((source.height * percent) / 100));
		const next = makeCanvas(width, height);
		const context = context2d(next);
		context.imageSmoothingQuality = 'high';
		context.drawImage(source, 0, 0, width, height);
		this.replaceLayerCanvas(
			layer,
			next,
			Math.round(centerX - width / 2),
			Math.round(centerY - height / 2)
		);
		this.invalidate();
		if (commit) {
			const before = this.transformBefore;
			this.transformBefore = null;
			this.commitStructure('Transform', before);
		} else {
			this.emit();
		}
	}

	fitActiveLayer(): void {
		const layer = this.activeLayer;
		if (!layer) return;
		const source = this.ensureLayerSource(layer);
		const factor = Math.min(this.doc.width / source.width, this.doc.height / source.height);
		this.scaleActiveLayer(Math.max(1, Math.round(factor * 100)), true);
	}

	beginLayerOpacity(index: number): DocSnapshot | null {
		return this.doc.layers[index] ? this.captureDoc() : null;
	}

	finishLayerOpacity(before: DocSnapshot | null): void {
		if (!before) return;
		this.commitStructure('Layer opacity', before);
	}

	flip(axis: 'horizontal' | 'vertical'): void {
		const doc = { width: this.doc.width, height: this.doc.height };
		this.mutate(axis === 'horizontal' ? 'Flip horizontal' : 'Flip vertical', () => {
			for (const layer of this.doc.layers) {
				const next = makeCanvas(layer.canvas.width, layer.canvas.height);
				const context = context2d(next);
				if (axis === 'horizontal') {
					context.translate(next.width, 0);
					context.scale(-1, 1);
				} else {
					context.translate(0, next.height);
					context.scale(1, -1);
				}
				context.drawImage(layer.canvas, 0, 0);
				const position = flipPosition(axis, doc, {
					x: layer.x,
					y: layer.y,
					width: layer.canvas.width,
					height: layer.canvas.height
				});
				layer.canvas = next;
				layer.x = position.x;
				layer.y = position.y;
				layer.source = undefined;
			}
			this.geometry = flipGeometry(this.geometry, axis);
			this.selection = null;
		});
		this.afterGeometry();
	}

	rotate(direction: 'cw' | 'ccw'): void {
		const doc = { width: this.doc.width, height: this.doc.height };
		this.mutate(direction === 'cw' ? 'Rotate right' : 'Rotate left', () => {
			for (const layer of this.doc.layers) {
				const width = layer.canvas.width;
				const height = layer.canvas.height;
				const next = makeCanvas(height, width);
				const context = context2d(next);
				if (direction === 'cw') {
					context.translate(height, 0);
					context.rotate(Math.PI / 2);
				} else {
					context.translate(0, width);
					context.rotate(-Math.PI / 2);
				}
				context.drawImage(layer.canvas, 0, 0);
				const position = rotatePosition(direction, doc, {
					x: layer.x,
					y: layer.y,
					width,
					height
				});
				layer.canvas = next;
				layer.x = position.x;
				layer.y = position.y;
				layer.source = undefined;
			}
			this.geometry = rotateGeometry(this.geometry, direction);
			this.doc.width = doc.height;
			this.doc.height = doc.width;
			this.selection = null;
		});
		this.afterGeometry();
	}

	resizeCanvas(mode: 'scale' | 'extend', width: number, height: number): void {
		const nextWidth = clampSide(width);
		const nextHeight = clampSide(height);
		if (nextWidth === this.doc.width && nextHeight === this.doc.height) return;
		const factorX = nextWidth / this.doc.width;
		const factorY = nextHeight / this.doc.height;
		const shiftX = Math.round((nextWidth - this.doc.width) / 2);
		const shiftY = Math.round((nextHeight - this.doc.height) / 2);
		this.mutate(mode === 'scale' ? 'Scale canvas' : 'Extend canvas', () => {
			if (mode === 'scale') {
				for (const layer of this.doc.layers) {
					const next = makeCanvas(layer.canvas.width * factorX, layer.canvas.height * factorY);
					const context = context2d(next);
					context.imageSmoothingQuality = 'high';
					context.drawImage(layer.canvas, 0, 0, next.width, next.height);
					layer.canvas = next;
					layer.x = Math.round(layer.x * factorX);
					layer.y = Math.round(layer.y * factorY);
					layer.source = undefined;
				}
			} else {
				for (const layer of this.doc.layers) {
					layer.x += shiftX;
					layer.y += shiftY;
				}
			}
			this.geometry =
				mode === 'scale'
					? scaleGeometry(this.geometry, nextWidth, nextHeight)
					: translateGeometry(this.geometry, shiftX, shiftY, nextWidth, nextHeight);
			this.doc.width = nextWidth;
			this.doc.height = nextHeight;
			this.selection = null;
		});
		this.afterGeometry();
	}

	private afterGeometry(): void {
		this.stopAnts();
		this.fit();
	}

	setSelection(path: Point[] | null, final: boolean): void {
		if (!path) {
			this.selection = null;
			this.draft = null;
			this.stopAnts();
		} else if (!final) {
			this.draft = path;
			this.startAnts();
		} else {
			this.draft = null;
			this.selection = buildSelection(path, this.doc.width, this.doc.height);
			if (this.selection) this.startAnts();
			else this.stopAnts();
		}
		this.emit();
		this.invalidate();
	}

	selectAll(): void {
		if (!this.ready) return;
		this.setSelection(rectPath({ x: 0, y: 0 }, { x: this.doc.width, y: this.doc.height }), true);
	}

	deselect(): void {
		this.setSelection(null, true);
	}

	layerSelectionMask(layer: Layer): Uint8ClampedArray | null {
		const selection = this.selection;
		if (!selection) return null;
		const key = layer.x * 100003 + layer.y * 7919 + layer.canvas.width * 131 + layer.canvas.height;
		let byLayer = this.maskCache.get(selection);
		if (!byLayer) {
			byLayer = new Map();
			this.maskCache.set(selection, byLayer);
		}
		const cached = byLayer.get(key);
		if (cached) return cached;
		const mask = sampleMaskForLayer(
			selection.mask,
			this.doc.width,
			this.doc.height,
			layer.canvas.width,
			layer.canvas.height,
			layer.x,
			layer.y
		);
		byLayer.set(key, mask);
		return mask;
	}

	clipToSelection(context: CanvasRenderingContext2D, layer: Layer): void {
		const selection = this.selection;
		if (!selection) return;
		context.beginPath();
		selection.path.forEach((p, i) => {
			const x = p.x - layer.x;
			const y = p.y - layer.y;
			if (i === 0) context.moveTo(x, y);
			else context.lineTo(x, y);
		});
		context.closePath();
		context.clip();
	}

	private selectionPiece(layer: Layer): HTMLCanvasElement | null {
		const selection = this.selection;
		const mask = this.layerSelectionMask(layer);
		if (!selection || !mask) return null;
		const context = context2d(layer.canvas);
		const image = context.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
		const extracted = extractByMask(image.data, mask);
		const scratch = makeCanvas(layer.canvas.width, layer.canvas.height);
		context2d(scratch).putImageData(
			new ImageData(new Uint8ClampedArray(extracted), image.width, image.height),
			0,
			0
		);
		const piece = makeCanvas(selection.bounds.width, selection.bounds.height);
		context2d(piece).drawImage(scratch, layer.x - selection.bounds.x, layer.y - selection.bounds.y);
		return piece;
	}

	copySelection(): boolean {
		const layer = this.activeLayer;
		const selection = this.selection;
		if (!layer || !selection) return false;
		const piece = this.selectionPiece(layer);
		if (!piece) return false;
		this.clipboard = { canvas: piece, x: selection.bounds.x, y: selection.bounds.y };
		this.emit();
		return true;
	}

	deleteSelection(label = 'Delete'): boolean {
		const layer = this.activeLayer;
		const selection = this.selection;
		const mask = layer ? this.layerSelectionMask(layer) : null;
		if (!layer || !selection || !mask) return false;
		const context = context2d(layer.canvas);
		const before = this.snapshotLayer(layer);
		const image = context.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
		clearByMask(image.data, mask);
		context.putImageData(image, 0, 0);
		this.commitPixels(
			label,
			layer,
			{
				x: selection.bounds.x - layer.x,
				y: selection.bounds.y - layer.y,
				width: selection.bounds.width,
				height: selection.bounds.height
			},
			before
		);
		this.emit();
		return true;
	}

	cutSelection(): void {
		if (!this.copySelection()) return;
		this.deleteSelection('Cut');
	}

	pasteClipboard(): void {
		const clip = this.clipboard;
		if (!clip || !this.ready) return;
		const x = this.selection
			? this.selection.bounds.x
			: Math.round((this.doc.width - clip.canvas.width) / 2);
		const y = this.selection
			? this.selection.bounds.y
			: Math.round((this.doc.height - clip.canvas.height) / 2);
		this.beginGroup();
		this.addPieceLayer(clip.canvas, x, y, 'Pasted');
		this.endGroup('Paste');
		this.setSelection(
			rectPath({ x, y }, { x: x + clip.canvas.width, y: y + clip.canvas.height }),
			true
		);
		this.setTool('move');
	}

	nudge(dx: number, dy: number): void {
		const layer = this.activeLayer;
		if (!layer) return;
		const before = this.captureDoc();
		layer.x += dx;
		layer.y += dy;
		if (this.selection) {
			this.setSelection(
				this.selection.path.map((p) => ({ x: p.x + dx, y: p.y + dy })),
				true
			);
		}
		this.commitStructure('Nudge', before);
	}

	private dropBases(): void {
		this.adjustBase = null;
		this.filterBase = null;
		if (this.filterState.active) this.scheduleFilterPreview();
	}

	setFilter(active: ActiveFilter | null): void {
		this.filterState = selectActive(this.filterState, active, active?.defaultIntensity);
		this.filterSteps = active ? active.steps.map((step) => ({ ...step })) : [];
		this.filterError = null;
		this.scheduleFilterPreview();
		this.emit();
	}

	setFilterIntensity(value: number): void {
		if (!this.filterState.active) return;
		this.filterState = withIntensity(this.filterState, value);
		this.scheduleFilterPreview();
		this.emit();
	}

	setFilterCompare(on: boolean): void {
		if (this.filterState.compare === on) return;
		this.filterState = withCompare(this.filterState, on);
		this.emit();
		this.invalidate();
	}

	clearFilter(): void {
		const wasFineTune = this.fineTune;
		this.discardFilter();
		if (wasFineTune) {
			this.toolId = 'filters';
			this.emit();
		}
	}

	clearFilterError(): void {
		if (this.filterError === null) return;
		this.filterError = null;
		this.emit();
	}

	openFineTune(): void {
		if (!this.filterState.active || this.toolId !== 'filters') return;
		this.fineTune = true;
		this.toolId = 'adjust';
		this.emit();
		this.invalidate();
	}

	closeFineTune(): void {
		if (!this.fineTune) return;
		this.fineTune = false;
		this.toolId = 'filters';
		this.emit();
		this.invalidate();
	}

	setFilterStepParam(index: number, paramId: string, value: number): void {
		if (!this.filterState.active) return;
		this.filterSteps = setStepParam(this.filterSteps, index, paramId, value);
		this.scheduleFilterPreview();
		this.emit();
	}

	toggleFilterStep(index: number): void {
		if (!this.filterState.active) return;
		this.filterSteps = toggleStep(this.filterSteps, index);
		this.scheduleFilterPreview();
		this.emit();
	}

	resetFilterSteps(): void {
		const active = this.filterState.active;
		if (!active) return;
		this.filterSteps = active.steps.map((step) => ({ ...step }));
		this.scheduleFilterPreview();
		this.emit();
	}

	thumbSource(): ImageData | null {
		const layer = this.activeLayer;
		return layer ? makeThumbSource(layer.canvas) : null;
	}

	private discardFilter(): void {
		this.filterToken++;
		this.filterState = initialToolState<ActiveFilter>();
		this.filterSteps = [];
		this.filterPreview = null;
		this.fineTune = false;
		this.emit();
		this.invalidate();
	}

	private scheduleFilterPreview(): void {
		const token = ++this.filterToken;
		if (!this.filterState.active) {
			this.filterPreview = null;
			this.invalidate();
			return;
		}
		setTimeout(() => this.computeFilterPreview(token), 0);
	}

	private filterBaseFor(layer: Layer, allowScale: boolean) {
		const longest = Math.max(layer.canvas.width, layer.canvas.height);
		const scale = allowScale ? Math.min(1, PREVIEW_LONG_EDGE / longest) : 1;
		const cached = this.filterBase;
		if (cached && cached.layer === layer && cached.canvas === layer.canvas && cached.scale === scale) {
			return cached;
		}
		let image: ImageData;
		if (scale === 1) {
			image = context2d(layer.canvas).getImageData(0, 0, layer.canvas.width, layer.canvas.height);
		} else {
			const small = makeCanvas(layer.canvas.width * scale, layer.canvas.height * scale);
			const context = context2d(small);
			context.imageSmoothingQuality = 'high';
			context.drawImage(layer.canvas, 0, 0, small.width, small.height);
			image = context.getImageData(0, 0, small.width, small.height);
		}
		const base = { layer, canvas: layer.canvas, scale, image };
		this.filterBase = base;
		return base;
	}

	private computeFilterPreview(token: number): void {
		const layer = this.activeLayer;
		const active = this.filterState.active;
		if (token !== this.filterToken || !layer || !active) return;
		try {
			const mask = this.layerSelectionMask(layer);
			const base = this.filterBaseFor(layer, mask === null);
			const output = renderRecipe(
				{ width: base.image.width, height: base.image.height, data: base.image.data },
				{ steps: this.filterSteps, cube: active.cube },
				this.filterState.intensity
			);
			const data = mask ? blendByMask(base.image.data, output.data, mask) : output.data;
			const canvas =
				this.filterPreview?.layer === layer &&
				this.filterPreview.canvas.width === output.width &&
				this.filterPreview.canvas.height === output.height
					? this.filterPreview.canvas
					: makeCanvas(output.width, output.height);
			context2d(canvas).putImageData(
				new ImageData(new Uint8ClampedArray(data), output.width, output.height),
				0,
				0
			);
			this.filterPreview = { layer, canvas };
		} catch (error) {
			this.failFilter(error);
			return;
		}
		this.invalidate();
	}

	private failFilter(error: unknown): void {
		const name = this.filterState.active?.name ?? 'The filter';
		const reason = error instanceof Error ? error.message : 'it could not be compiled';
		const wasFineTune = this.fineTune;
		this.discardFilter();
		if (wasFineTune) this.toolId = 'filters';
		this.filterError = `${name} could not be applied: ${reason}`;
		this.emit();
	}

	applyFilterToLayer(): void {
		const layer = this.activeLayer;
		const active = this.filterState.active;
		if (!layer || !active || !canApply(this.filterState)) return;
		try {
			const context = context2d(layer.canvas);
			const image = context.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
			const output = renderRecipe(
				{ width: image.width, height: image.height, data: image.data },
				{ steps: this.filterSteps, cube: active.cube },
				this.filterState.intensity
			);
			const mask = this.layerSelectionMask(layer);
			const data = mask ? blendByMask(image.data, output.data, mask) : output.data;
			const before = this.snapshotLayer(layer);
			context.putImageData(new ImageData(new Uint8ClampedArray(data), image.width, image.height), 0, 0);
			this.commitPixels(
				historyLabel(active.name, this.filterState.intensity),
				layer,
				{ x: 0, y: 0, width: layer.canvas.width, height: layer.canvas.height },
				before
			);
		} catch (error) {
			this.failFilter(error);
			return;
		}
		this.clearFilter();
		this.invalidate();
	}

	private adjustSteps(): FilterStep[] {
		return paintFilters.list().map((filter) => ({
			filter,
			values: this.adjustValues[filter.id] ?? defaultFilterValues(filter)
		}));
	}

	private ensureAdjustValues(): void {
		for (const filter of paintFilters.list()) {
			if (!this.adjustValues[filter.id]) this.adjustValues[filter.id] = defaultFilterValues(filter);
		}
	}

	setAdjustValue(filterId: string, key: string, value: number | boolean): void {
		this.adjustValues = {
			...this.adjustValues,
			[filterId]: { ...(this.adjustValues[filterId] ?? {}), [key]: value }
		};
		this.scheduleAdjust();
		this.emit();
	}

	resetAdjust(refresh = true): void {
		this.adjustValues = {};
		this.ensureAdjustValues();
		if (refresh) {
			this.scheduleAdjust();
			this.emit();
		}
	}

	private discardAdjust(): void {
		this.adjustPreview = null;
		this.adjustToken++;
		this.resetAdjust(false);
		this.invalidate();
	}

	private scheduleAdjust(): void {
		const token = ++this.adjustToken;
		void this.computeAdjust(token);
	}

	private async computeAdjust(token: number): Promise<void> {
		const layer = this.activeLayer;
		if (!layer || this.toolId !== 'adjust' || this.fineTune) return;
		const steps = this.adjustSteps();
		if (!steps.some((step) => step.filter.active(step.values))) {
			this.adjustPreview = null;
			this.invalidate();
			return;
		}
		const result = await this.runAdjust(layer, steps);
		if (token !== this.adjustToken || !result) return;
		const canvas =
			this.adjustPreview?.layer === layer &&
			this.adjustPreview.canvas.width === layer.canvas.width &&
			this.adjustPreview.canvas.height === layer.canvas.height
				? this.adjustPreview.canvas
				: makeCanvas(layer.canvas.width, layer.canvas.height);
		context2d(canvas).putImageData(result, 0, 0);
		this.adjustPreview = { layer, canvas };
		this.invalidate();
	}

	private async runAdjust(layer: Layer, steps: FilterStep[]): Promise<ImageData | null> {
		if (
			!this.adjustBase ||
			this.adjustBase.layer !== layer ||
			this.adjustBase.canvas !== layer.canvas
		) {
			this.adjustBase = {
				layer,
				canvas: layer.canvas,
				image: context2d(layer.canvas).getImageData(0, 0, layer.canvas.width, layer.canvas.height)
			};
		}
		const base = this.adjustBase.image;
		const mask = this.layerSelectionMask(layer);
		const output = await runFilters(
			{ width: base.width, height: base.height, data: base.data },
			steps,
			mask
		);
		return new ImageData(new Uint8ClampedArray(output.data), output.width, output.height);
	}

	async applyAdjust(): Promise<void> {
		const layer = this.activeLayer;
		if (!layer) return;
		const steps = this.adjustSteps();
		if (!steps.some((step) => step.filter.active(step.values))) return;
		const result = await this.runAdjust(layer, steps);
		if (!result) return;
		this.adjustToken++;
		const before = this.snapshotLayer(layer);
		context2d(layer.canvas).putImageData(result, 0, 0);
		this.adjustPreview = null;
		this.commitPixels(
			'Adjust colours',
			layer,
			{
				x: 0,
				y: 0,
				width: layer.canvas.width,
				height: layer.canvas.height
			},
			before
		);
		this.resetAdjust(false);
		this.dropBases();
		this.emit();
		this.invalidate();
	}

	private startAnts(): void {
		if (this.antsTimer || typeof window === 'undefined') return;
		if (
			typeof matchMedia === 'function' &&
			matchMedia('(prefers-reduced-motion: reduce)').matches
		) {
			return;
		}
		this.antsTimer = setInterval(() => {
			this.ants = (this.ants + 1) % 10;
			this.invalidate();
		}, 120);
	}

	private startAntsIfNeeded(): void {
		if (this.selection || this.draft) this.startAnts();
		else this.stopAnts();
	}

	private stopAnts(): void {
		if (this.antsTimer) clearInterval(this.antsTimer);
		this.antsTimer = null;
	}

	private clientPoint(event: { clientX: number; clientY: number }): Point {
		const box = this.stage?.getBoundingClientRect();
		return { x: event.clientX - (box?.left ?? 0), y: event.clientY - (box?.top ?? 0) };
	}

	private toolPointer(event: PointerEvent): ToolPointer {
		return {
			pressure: event.pressure || 0.5,
			pointerType: event.pointerType,
			altKey: event.altKey,
			shiftKey: event.shiftKey
		};
	}

	onPointerDown(event: PointerEvent): void {
		if (!this.ready || !this.stage) return;
		const screen = this.clientPoint(event);
		this.pointers.set(event.pointerId, screen);

		if (this.pointers.size === 2) {
			this.cancelActive();
			const [a, b] = [...this.pointers.values()];
			this.pinch = {
				distance: Math.hypot(a.x - b.x, a.y - b.y),
				view: { ...this.view },
				center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
			};
			return;
		}
		if (this.pointers.size > 2) return;

		if (event.button === 1 || this.spaceHeld) {
			this.panning = { x: screen.x, y: screen.y, view: { ...this.view } };
			this.activePointerId = event.pointerId;
			this.emit();
			return;
		}
		if (event.button !== 0) return;

		const tool = this.tool;
		if (!tool?.pointerDown) return;
		this.activePointerId = event.pointerId;
		tool.pointerDown(this, screenToDoc(this.view, screen), this.toolPointer(event));
	}

	onPointerMove(event: PointerEvent): void {
		if (!this.stage) return;
		const screen = this.clientPoint(event);
		if (this.pointers.has(event.pointerId)) this.pointers.set(event.pointerId, screen);
		this.cursor = screenToDoc(this.view, screen);

		if (this.pinch && this.pointers.size >= 2) {
			const [a, b] = [...this.pointers.values()];
			const distance = Math.hypot(a.x - b.x, a.y - b.y);
			const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
			const zoomed = zoomAbout(this.pinch.view, distance / this.pinch.distance, this.pinch.center);
			this.view = panBy(zoomed, center.x - this.pinch.center.x, center.y - this.pinch.center.y);
			this.autoFit = false;
			this.emit();
			this.invalidate();
			return;
		}
		if (this.panning && event.pointerId === this.activePointerId) {
			this.view = panBy(this.panning.view, screen.x - this.panning.x, screen.y - this.panning.y);
			this.autoFit = false;
			this.invalidate();
			return;
		}
		if (event.pointerId === this.activePointerId) {
			this.tool?.pointerMove?.(this, this.cursor, this.toolPointer(event));
		}
		this.invalidate();
	}

	onPointerUp(event: PointerEvent): void {
		const screen = this.clientPoint(event);
		this.pointers.delete(event.pointerId);
		if (this.pinch) {
			if (this.pointers.size < 2) this.pinch = null;
			return;
		}
		if (this.panning && event.pointerId === this.activePointerId) {
			this.panning = null;
			this.activePointerId = null;
			this.emit();
			return;
		}
		if (event.pointerId === this.activePointerId) {
			this.activePointerId = null;
			this.tool?.pointerUp?.(this, screenToDoc(this.view, screen), this.toolPointer(event));
			this.emit();
		}
	}

	onPointerCancel(event: PointerEvent): void {
		this.pointers.delete(event.pointerId);
		if (event.pointerId === this.activePointerId) this.cancelActive();
		if (this.pointers.size < 2) this.pinch = null;
	}

	onPointerLeave(): void {
		this.cursor = null;
		this.invalidate();
	}

	onWheel(event: WheelEvent): void {
		if (!this.ready) return;
		event.preventDefault();
		this.zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12, this.clientPoint(event));
	}

	private cancelActive(): void {
		if (this.activePointerId === null && !this.panning) return;
		this.tool?.pointerCancel?.(this);
		this.panning = null;
		this.activePointerId = null;
	}

	get cursorStyle(): string {
		if (this.panning) return 'grabbing';
		if (this.spaceHeld) return 'grab';
		const kind = this.tool?.cursor;
		if (kind === 'brush') return 'none';
		return kind === 'crosshair' ? 'crosshair' : 'default';
	}

	private render(): void {
		const context = this.context;
		const theme = this.theme;
		if (!context || !theme || !this.stage) return;
		const dpr = this.dpr;
		const { zoom, panX, panY } = this.view;

		context.setTransform(1, 0, 0, 1, 0, 0);
		context.clearRect(0, 0, this.stage.width, this.stage.height);
		if (!this.ready) return;

		context.setTransform(dpr * zoom, 0, 0, dpr * zoom, dpr * panX, dpr * panY);
		context.save();
		context.beginPath();
		context.rect(0, 0, this.doc.width, this.doc.height);
		context.clip();

		if (!this.pattern) this.pattern = this.makePattern(context, theme);
		if (this.pattern) {
			this.pattern.setTransform(new DOMMatrix().scale(1 / zoom));
			context.fillStyle = this.pattern;
			context.fillRect(0, 0, this.doc.width, this.doc.height);
		}

		context.imageSmoothingEnabled = zoom < 4;
		for (const layer of this.doc.layers) {
			if (!layer.visible) continue;
			context.globalAlpha = layer.opacity;
			const adjusted = this.adjustPreview && this.adjustPreview.layer === layer;
			const filtered =
				this.filterPreview && this.filterPreview.layer === layer && !this.filterState.compare;
			if (filtered) {
				context.drawImage(
					this.filterPreview!.canvas,
					layer.x,
					layer.y,
					layer.canvas.width,
					layer.canvas.height
				);
			} else {
				context.drawImage(adjusted ? this.adjustPreview!.canvas : layer.canvas, layer.x, layer.y);
			}
			if (this.preview && this.preview.layer === layer) {
				context.globalAlpha = layer.opacity * this.preview.alpha;
				context.drawImage(this.preview.canvas, layer.x, layer.y);
			}
		}
		context.globalAlpha = 1;
		context.restore();

		context.lineWidth = 1 / zoom;
		context.strokeStyle = theme.line;
		context.strokeRect(0, 0, this.doc.width, this.doc.height);

		this.drawSelection(context, theme);
		if (!this.spaceHeld && !this.panning) this.tool?.overlay?.(this, context);
	}

	private drawSelection(context: CanvasRenderingContext2D, theme: EditorTheme): void {
		const path = this.draft ?? this.selection?.path ?? null;
		if (!path || path.length < 2) return;
		const zoom = this.view.zoom;
		context.save();
		context.beginPath();
		path.forEach((p, i) => (i === 0 ? context.moveTo(p.x, p.y) : context.lineTo(p.x, p.y)));
		context.closePath();
		context.lineWidth = 1.5 / zoom;
		context.setLineDash([5 / zoom, 5 / zoom]);
		context.strokeStyle = theme.canvas;
		context.stroke();
		context.strokeStyle = theme.fg;
		context.lineDashOffset = this.ants / zoom;
		context.stroke();
		context.setLineDash([]);

		const bounds = this.selection?.bounds;
		if (bounds && !this.draft) {
			const arm = Math.max(4, Math.min(14, bounds.width / 3, bounds.height / 3)) / zoom;
			context.lineWidth = 2.5 / zoom;
			context.strokeStyle = theme.fg;
			const corners: Array<[number, number, number, number]> = [
				[bounds.x, bounds.y, 1, 1],
				[bounds.x + bounds.width, bounds.y, -1, 1],
				[bounds.x, bounds.y + bounds.height, 1, -1],
				[bounds.x + bounds.width, bounds.y + bounds.height, -1, -1]
			];
			for (const [x, y, dx, dy] of corners) {
				context.beginPath();
				context.moveTo(x + dx * arm, y);
				context.lineTo(x, y);
				context.lineTo(x, y + dy * arm);
				context.stroke();
			}
		}
		context.restore();
	}

	private makePattern(context: CanvasRenderingContext2D, theme: EditorTheme): CanvasPattern | null {
		const tile = makeCanvas(16, 16);
		const tileContext = tile.getContext('2d');
		if (!tileContext) return null;
		tileContext.fillStyle = theme.surface2;
		tileContext.fillRect(0, 0, 16, 16);
		tileContext.fillStyle = theme.surface3;
		tileContext.fillRect(0, 0, 8, 8);
		tileContext.fillRect(8, 8, 8, 8);
		return context.createPattern(tile, 'repeat');
	}
}
