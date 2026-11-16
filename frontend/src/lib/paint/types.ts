export interface Point {
	x: number;
	y: number;
}

export interface Rect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface PixelBuffer {
	width: number;
	height: number;
	data: Uint8ClampedArray;
}

export type Rgba = readonly [number, number, number, number];

export interface View {
	zoom: number;
	panX: number;
	panY: number;
}

export interface Layer {
	id: number;
	name: string;
	canvas: HTMLCanvasElement;
	x: number;
	y: number;
	visible: boolean;
	opacity: number;
	source?: HTMLCanvasElement;
}

export interface PaintDocument {
	width: number;
	height: number;
	layers: Layer[];
	activeIndex: number;
}

export interface Selection {
	path: Point[];
	mask: Uint8ClampedArray;
	bounds: Rect;
}

export type OptionKey = 'size' | 'opacity' | 'color' | 'tolerance';

export type ToolPanelKind = 'options' | 'adjust' | 'filters' | 'crop' | 'transform' | 'selection';

export interface ToolSettings {
	color: string;
	size: number;
	opacity: number;
	tolerance: number;
}

export interface ToolPointer {
	pressure: number;
	pointerType: string;
	altKey: boolean;
	shiftKey: boolean;
}

export interface LayerState {
	layer: Layer;
	name: string;
	canvas: HTMLCanvasElement;
	x: number;
	y: number;
	visible: boolean;
	opacity: number;
	source: HTMLCanvasElement | undefined;
}

export interface DocGeometry {
	originalWidth: number;
	originalHeight: number;
	width: number;
	height: number;
	a: number;
	b: number;
	c: number;
	d: number;
	e: number;
	f: number;
}

export interface DocSnapshot {
	width: number;
	height: number;
	activeIndex: number;
	layers: LayerState[];
	selection: Selection | null;
	geometry: DocGeometry;
}

export interface ToolHost {
	readonly settings: ToolSettings;
	readonly activeLayer: Layer | null;
	readonly zoom: number;
	readonly cursor: Point | null;
	readonly cursorColor: string;
	readonly docWidth: number;
	readonly docHeight: number;
	readonly selection: Selection | null;
	readonly cropRect: Rect | null;
	invalidate(): void;
	snapshotLayer(layer: Layer): HTMLCanvasElement;
	setStrokePreview(layer: Layer | null, canvas: HTMLCanvasElement | null, alpha: number): void;
	commitPixels(label: string, layer: Layer, dirty: Rect, before: HTMLCanvasElement): void;
	pickColor(point: Point): void;
	clipToSelection(context: CanvasRenderingContext2D, layer: Layer): void;
	layerSelectionMask(layer: Layer): Uint8ClampedArray | null;
	setSelection(path: Point[] | null, final: boolean): void;
	captureDoc(): DocSnapshot;
	commitStructure(label: string, before: DocSnapshot): void;
	beginGroup(): void;
	endGroup(label: string, discard?: boolean): void;
	addPieceLayer(canvas: HTMLCanvasElement, x: number, y: number, name: string): Layer;
	ensureLayerSource(layer: Layer): HTMLCanvasElement;
	replaceLayerCanvas(layer: Layer, canvas: HTMLCanvasElement, x: number, y: number): void;
	setCropRect(rect: Rect | null): void;
}

export interface PaintTool {
	id: string;
	label: string;
	icon: string;
	key?: string;
	group: string;
	caption?: string;
	options?: OptionKey[];
	panel?: ToolPanelKind;
	hint?: string;
	cursor?: 'brush' | 'crosshair' | 'default';
	pointerDown?(host: ToolHost, point: Point, pointer: ToolPointer): void;
	pointerMove?(host: ToolHost, point: Point, pointer: ToolPointer): void;
	pointerUp?(host: ToolHost, point: Point, pointer: ToolPointer): void;
	pointerCancel?(host: ToolHost): void;
	overlay?(host: ToolHost, context: CanvasRenderingContext2D): void;
}

export type FilterValues = Record<string, number | boolean>;

export interface FilterParam {
	id: string;
	label: string;
	min: number;
	max: number;
	value: number;
	unit?: string;
	step?: number;
}

export interface PaintFilter {
	id: string;
	label: string;
	params: FilterParam[];
	toggle?: boolean;
	plot?: boolean;
	active(values: FilterValues): boolean;
	apply(image: PixelBuffer, values: FilterValues): PixelBuffer | Promise<PixelBuffer>;
	kind?: 'colour' | 'spatial';
	map?: (rgb: [number, number, number], values: Record<string, unknown>) => [number, number, number];
}

export interface PickedImage {
	canvas: HTMLCanvasElement;
	name: string;
}

export interface ImageSource {
	id: string;
	label: string;
	pick(): Promise<{ url?: string; blob?: Blob; canvas?: HTMLCanvasElement; name: string } | null>;
}

export interface EditorApi {
	flatten(): HTMLCanvasElement;
	addImage(canvas: HTMLCanvasElement, name: string): void;
	openImage(canvas: HTMLCanvasElement, name: string): void;
}

export interface PaintAction {
	id: string;
	label: string;
	run(editor: EditorApi): void | Promise<void>;
}
