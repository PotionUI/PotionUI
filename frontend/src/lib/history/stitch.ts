/**
 * Composition maths for the History "Stitch" tool: which parameter keys are
 * offerable across a selection, what text goes under each image, where every
 * tile lands on the output canvas, and the draw calls that paint it. Kept free
 * of DOM lookups (the 2D context is injected) so the whole thing is unit
 * testable under vitest's node environment.
 */

export type StitchLayoutMode = 'row' | 'column' | 'grid';
export type StitchTileMaxSide = 'original' | 1024 | 512;
export type StitchBackground = 'dark' | 'light' | 'transparent';

export interface StitchOptions {
	layout: StitchLayoutMode;
	columns: number;
	tileMaxSide: StitchTileMaxSide;
	gap: number;
	background: StitchBackground;
	showParams: boolean;
	paramKeys: string[];
}

/** One selected image, with everything the composition needs about it. */
export interface StitchItem {
	id: string;
	width: number;
	height: number;
	parameters: Record<string, unknown>;
	prompt: string;
}

export interface ParamKeyOption {
	key: string;
	label: string;
	/** Derived from the file/generation rather than read from `parameters`. */
	synthetic: boolean;
}

export interface ParamLine {
	key: string;
	label: string;
	value: string;
}

/** Checked by default when present in the selection, in this order. */
export const DEFAULT_PARAM_KEYS = [
	'seed',
	'steps',
	'cfg',
	'cfg_scale',
	'true_cfg_scale',
	'sampler',
	'scheduler',
	'model',
	'resolution'
];

export const DEFAULT_STITCH_OPTIONS: StitchOptions = {
	layout: 'row',
	columns: 2,
	tileMaxSide: 1024,
	gap: 16,
	background: 'dark',
	showParams: true,
	paramKeys: []
};

export const STITCH_FONT_STACK = 'ui-monospace, SFMono-Regular, Menlo, monospace';

/** Raster output has no access to the theme tokens, so it carries its own pair. */
export const STITCH_PALETTES: Record<StitchBackground, { background: string; text: string }> = {
	dark: { background: '#0f0f11', text: '#e7e7ea' },
	light: { background: '#f6f6f7', text: '#18181b' },
	transparent: { background: 'transparent', text: '#e7e7ea' }
};

const MIN_FONT_SIZE = 12;
const FONT_SIZE_PER_PX = 0.028;
const LINE_HEIGHT_RATIO = 1.35;
const LABEL_PADDING_RATIO = 0.4;
const MAX_VALUE_CHARS = 120;

const MAX_CANVAS_SIDE = 16384;
const MAX_CANVAS_PIXELS = 80_000_000;
const TILE_SIDE_LADDER: StitchTileMaxSide[] = ['original', 1024, 512];

const MODEL_EXTENSIONS = ['.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf', '.sft'];

export function paramLabel(key: string): string {
	return key.replace(/_/g, ' ');
}

function basename(value: string): string {
	const last = value.split(/[\\/]/).pop() ?? value;
	const lowered = last.toLowerCase();
	const extension = MODEL_EXTENSIONS.find((ext) => lowered.endsWith(ext));
	return extension ? last.slice(0, last.length - extension.length) : last;
}

function formatNumber(value: number): string {
	if (Number.isInteger(value)) return String(value);
	return String(Number(value.toFixed(4)));
}

function isModelKey(key: string): boolean {
	return key === 'model' || key.endsWith('_model') || key.endsWith('model_name');
}

function truncate(value: string): string {
	return value.length > MAX_VALUE_CHARS ? `${value.slice(0, MAX_VALUE_CHARS - 1)}…` : value;
}

/** The raw value for `key`, falling back to the synthetic sources. */
function rawValueFor(item: StitchItem, key: string): unknown {
	const own = item.parameters?.[key];
	if (own !== undefined && own !== null && own !== '') return own;
	if (key === 'resolution' && item.width > 0 && item.height > 0) {
		return `${Math.round(item.width)}×${Math.round(item.height)}`;
	}
	if (key === 'prompt') return item.prompt?.trim() || undefined;
	return undefined;
}

/** `null` when the image has nothing to show for this key. */
export function formatParamValue(key: string, raw: unknown): string | null {
	if (raw === undefined || raw === null) return null;
	if (typeof raw === 'number') return Number.isFinite(raw) ? formatNumber(raw) : null;
	if (typeof raw === 'boolean') return raw ? 'true' : 'false';
	if (typeof raw === 'string') {
		const trimmed = raw.trim();
		if (!trimmed) return null;
		const collapsed = trimmed.replace(/\s+/g, ' ');
		if (isModelKey(key) || /[\\/]/.test(collapsed)) return truncate(basename(collapsed));
		return truncate(collapsed);
	}
	try {
		const encoded = JSON.stringify(raw);
		if (!encoded || encoded === '{}' || encoded === '[]') return null;
		return truncate(encoded);
	} catch {
		return null;
	}
}

/**
 * Every key the selection can show, in first-seen order across the images,
 * with `resolution` and `prompt` appended when no real parameter already
 * owns those names.
 */
export function collectParamKeys(items: StitchItem[]): ParamKeyOption[] {
	const seen = new Set<string>();
	const options: ParamKeyOption[] = [];

	for (const item of items) {
		for (const key of Object.keys(item.parameters ?? {})) {
			if (seen.has(key)) continue;
			const hasValue = items.some((candidate) => formatParamValue(key, rawValueFor(candidate, key)) !== null);
			if (!hasValue) continue;
			seen.add(key);
			options.push({ key, label: paramLabel(key), synthetic: false });
		}
	}

	for (const key of ['resolution', 'prompt']) {
		if (seen.has(key)) continue;
		const hasValue = items.some((item) => formatParamValue(key, rawValueFor(item, key)) !== null);
		if (!hasValue) continue;
		seen.add(key);
		options.push({ key, label: paramLabel(key), synthetic: true });
	}

	return options;
}

export function defaultParamKeys(available: ParamKeyOption[]): string[] {
	return DEFAULT_PARAM_KEYS.filter((key) => available.some((option) => option.key === key));
}

/** The lines drawn under one image; keys the image lacks are simply dropped. */
export function paramLinesFor(item: StitchItem, keys: string[]): ParamLine[] {
	const lines: ParamLine[] = [];
	for (const key of keys) {
		const value = formatParamValue(key, rawValueFor(item, key));
		if (value === null) continue;
		lines.push({ key, label: paramLabel(key), value });
	}
	return lines;
}

export interface StitchTileInput {
	width: number;
	height: number;
	/** How many parameter lines this image will draw. */
	lines?: number;
}

export interface StitchRect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface StitchTileLayout {
	image: StitchRect;
	label: StitchRect;
	fontSize: number;
	lineHeight: number;
	padding: number;
}

export interface StitchLayout {
	width: number;
	height: number;
	columns: number;
	rows: number;
	tiles: StitchTileLayout[];
	/** The side actually used, which may be smaller than the one asked for. */
	tileMaxSide: StitchTileMaxSide;
	clamped: boolean;
}

function columnCount(count: number, options: StitchOptions): number {
	if (count <= 0) return 1;
	if (options.layout === 'column') return 1;
	if (options.layout === 'row') return count;
	const asked = Math.round(options.columns);
	if (!Number.isFinite(asked)) return 1;
	return Math.min(Math.max(asked, 1), count);
}

function scaleTile(tile: StitchTileInput, maxSide: StitchTileMaxSide) {
	const width = Math.max(1, Math.round(tile.width));
	const height = Math.max(1, Math.round(tile.height));
	if (maxSide === 'original') return { width, height };
	const longest = Math.max(width, height);
	if (longest <= maxSide) return { width, height };
	const factor = maxSide / longest;
	return { width: Math.max(1, Math.round(width * factor)), height: Math.max(1, Math.round(height * factor)) };
}

function layoutAt(
	tiles: StitchTileInput[],
	options: StitchOptions,
	maxSide: StitchTileMaxSide
): StitchLayout {
	const gap = Math.max(0, Math.round(options.gap));
	const columns = columnCount(tiles.length, options);
	const rows = tiles.length > 0 ? Math.ceil(tiles.length / columns) : 0;

	const cells = tiles.map((tile) => {
		const scaled = scaleTile(tile, maxSide);
		const fontSize = Math.max(MIN_FONT_SIZE, Math.round(scaled.width * FONT_SIZE_PER_PX));
		const lineHeight = Math.round(fontSize * LINE_HEIGHT_RATIO);
		const padding = Math.round(fontSize * LABEL_PADDING_RATIO);
		const lineCount = options.showParams ? Math.max(0, Math.round(tile.lines ?? 0)) : 0;
		const labelHeight = lineCount > 0 ? padding * 2 + lineCount * lineHeight : 0;
		return { ...scaled, fontSize, lineHeight, padding, labelHeight };
	});

	const columnWidths = new Array(columns).fill(0);
	const rowImageHeights = new Array(Math.max(rows, 0)).fill(0);
	const rowLabelHeights = new Array(Math.max(rows, 0)).fill(0);

	cells.forEach((cell, index) => {
		const column = index % columns;
		const row = Math.floor(index / columns);
		columnWidths[column] = Math.max(columnWidths[column], cell.width);
		rowImageHeights[row] = Math.max(rowImageHeights[row], cell.height);
		rowLabelHeights[row] = Math.max(rowLabelHeights[row], cell.labelHeight);
	});

	const columnOffsets: number[] = [];
	let x = gap;
	for (let column = 0; column < columns; column += 1) {
		columnOffsets.push(x);
		x += columnWidths[column] + gap;
	}

	const rowOffsets: number[] = [];
	let y = gap;
	for (let row = 0; row < rows; row += 1) {
		rowOffsets.push(y);
		y += rowImageHeights[row] + rowLabelHeights[row] + gap;
	}

	const layoutTiles: StitchTileLayout[] = cells.map((cell, index) => {
		const column = index % columns;
		const row = Math.floor(index / columns);
		const cellX = columnOffsets[column];
		const cellY = rowOffsets[row];
		return {
			image: {
				x: cellX + Math.round((columnWidths[column] - cell.width) / 2),
				y: cellY + (rowImageHeights[row] - cell.height),
				width: cell.width,
				height: cell.height
			},
			label: {
				x: cellX,
				y: cellY + rowImageHeights[row],
				width: columnWidths[column],
				height: cell.labelHeight
			},
			fontSize: cell.fontSize,
			lineHeight: cell.lineHeight,
			padding: cell.padding
		};
	});

	return {
		width: tiles.length > 0 ? x : 0,
		height: tiles.length > 0 ? y : 0,
		columns,
		rows,
		tiles: layoutTiles,
		tileMaxSide: maxSide,
		clamped: false
	};
}

function fitsCanvas(layout: StitchLayout): boolean {
	if (layout.width > MAX_CANVAS_SIDE || layout.height > MAX_CANVAS_SIDE) return false;
	return layout.width * layout.height <= MAX_CANVAS_PIXELS;
}

/**
 * Tile rects, label boxes and total canvas size. A composition that would
 * exceed what a browser canvas can hold steps `tileMaxSide` down the ladder
 * and comes back `clamped`.
 */
export function computeLayout(tiles: StitchTileInput[], options: StitchOptions): StitchLayout {
	const start = Math.max(0, TILE_SIDE_LADDER.indexOf(options.tileMaxSide));
	let last = layoutAt(tiles, options, TILE_SIDE_LADDER[start]);
	if (fitsCanvas(last)) return last;

	for (let step = start + 1; step < TILE_SIDE_LADDER.length; step += 1) {
		last = layoutAt(tiles, options, TILE_SIDE_LADDER[step]);
		if (fitsCanvas(last)) return { ...last, clamped: true };
	}

	return { ...last, clamped: true };
}

/** The slice of a 2D canvas context the stitch draw uses. */
export interface StitchDrawTarget {
	fillStyle: string | CanvasGradient | CanvasPattern;
	font: string;
	textBaseline: CanvasTextBaseline;
	clearRect(x: number, y: number, width: number, height: number): void;
	fillRect(x: number, y: number, width: number, height: number): void;
	drawImage(image: CanvasImageSource, x: number, y: number, width: number, height: number): void;
	fillText(text: string, x: number, y: number, maxWidth?: number): void;
}

export function drawStitch(
	ctx: StitchDrawTarget,
	images: ReadonlyArray<CanvasImageSource | null>,
	layout: StitchLayout,
	lines: ReadonlyArray<ParamLine[]>,
	options: StitchOptions
): void {
	const palette = STITCH_PALETTES[options.background];

	if (options.background === 'transparent') {
		ctx.clearRect(0, 0, layout.width, layout.height);
	} else {
		ctx.fillStyle = palette.background;
		ctx.fillRect(0, 0, layout.width, layout.height);
	}

	layout.tiles.forEach((tile, index) => {
		const image = images[index];
		if (image) {
			ctx.drawImage(image, tile.image.x, tile.image.y, tile.image.width, tile.image.height);
		}

		if (!options.showParams) return;
		const tileLines = lines[index] ?? [];
		if (tileLines.length === 0) return;

		ctx.font = `${tile.fontSize}px ${STITCH_FONT_STACK}`;
		ctx.textBaseline = 'top';
		ctx.fillStyle = palette.text;

		const maxWidth = Math.max(1, tile.label.width - tile.padding * 2);
		let y = tile.label.y + tile.padding;
		for (const line of tileLines) {
			ctx.fillText(`${line.label} ${line.value}`, tile.label.x + tile.padding, y, maxWidth);
			y += tile.lineHeight;
		}
	});
}

/** Preview zoom is relative to the fit-to-pane scale, where 1 means "fit". */
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 4;
export const ZOOM_STEP = 1.25;

/** What a preview canvas may cost before the display falls back to CSS scaling. */
export interface PreviewCap {
	maxSide: number;
	maxPixels: number;
}

export const DEFAULT_PREVIEW_CAP: PreviewCap = { maxSide: 4096, maxPixels: 16_000_000 };

export interface PreviewSize {
	width: number;
	height: number;
}

export function clampZoom(zoom: number): number {
	if (!Number.isFinite(zoom)) return 1;
	return Math.min(Math.max(zoom, MIN_ZOOM), MAX_ZOOM);
}

/** One notch in or out, snapped to the bounds rather than overshooting them. */
export function zoomStep(zoom: number, direction: 1 | -1): number {
	const current = clampZoom(zoom);
	return clampZoom(direction > 0 ? current * ZOOM_STEP : current / ZOOM_STEP);
}

/** The scale that fits `content` inside `viewport`, never magnifying. */
export function fitScale(content: PreviewSize, viewport: PreviewSize): number {
	if (content.width <= 0 || content.height <= 0) return 1;
	if (viewport.width <= 0 || viewport.height <= 0) return 1;
	return Math.min(1, viewport.width / content.width, viewport.height / content.height);
}

export interface PreviewScale {
	/** What the 2D context is scaled by before the composition is drawn. */
	renderScale: number;
	/** Canvas backing store, in device pixels. */
	width: number;
	height: number;
	/** On-screen size, in CSS pixels. */
	displayWidth: number;
	displayHeight: number;
	/** The backing store hit the cap, so the display is a CSS blow-up of it. */
	capped: boolean;
}

/**
 * Redrawing at the zoomed scale keeps the parameter text crisp, so the render
 * scale follows the zoom - up to `cap`, past which the canvas stops growing
 * and the browser stretches what is there.
 */
export function previewScaleFor(
	fit: number,
	zoom: number,
	content: PreviewSize,
	cap: PreviewCap = DEFAULT_PREVIEW_CAP
): PreviewScale {
	if (content.width <= 0 || content.height <= 0) {
		return { renderScale: 0, width: 0, height: 0, displayWidth: 0, displayHeight: 0, capped: false };
	}

	const target = Math.max(0, fit) * clampZoom(zoom);
	const displayWidth = Math.max(1, Math.round(content.width * target));
	const displayHeight = Math.max(1, Math.round(content.height * target));

	const shrinks = [1];
	if (displayWidth > cap.maxSide) shrinks.push(cap.maxSide / displayWidth);
	if (displayHeight > cap.maxSide) shrinks.push(cap.maxSide / displayHeight);
	const pixels = displayWidth * displayHeight;
	if (pixels > cap.maxPixels) shrinks.push(Math.sqrt(cap.maxPixels / pixels));
	const shrink = Math.min(...shrinks);

	const renderScale = target * shrink;
	return {
		renderScale,
		width: Math.max(1, Math.round(content.width * renderScale)),
		height: Math.max(1, Math.round(content.height * renderScale)),
		displayWidth,
		displayHeight,
		capped: shrink < 1
	};
}

export function stitchFileName(now: Date): string {
	const pad = (value: number) => String(value).padStart(2, '0');
	const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
	return `stitch-${stamp}.png`;
}
