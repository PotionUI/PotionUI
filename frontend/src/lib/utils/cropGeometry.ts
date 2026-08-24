/**
 * Transform math for the "Prepare" (crop-to-cell) image editor, kept outside
 * the component so it can be tested - vitest runs `environment: 'node'`, so
 * anything touching a canvas or an <img> is untestable here (same reason as
 * form-fields/sliderGeometry.ts).
 *
 * Coordinate model: everything is expressed in CELL pixels. The transform is
 * centre-relative - `offsetX/offsetY` displace the image's centre from the
 * cell's centre - so "centred" is always `{0, 0}` regardless of zoom or cell
 * size, and a cell resize doesn't silently re-frame the image.
 */

export interface Size {
	width: number;
	height: number;
}

export interface Rect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface CropTransform {
	/** Image pixels -> cell pixels. */
	zoom: number;
	/** Image centre displacement from the cell centre, in cell pixels. */
	offsetX: number;
	offsetY: number;
}

export const CELL_MIN = 16;
export const CELL_MAX = 4096;
export const ZOOM_MIN = 0.01;
export const ZOOM_MAX = 32;
/** Margin is capped at a quarter of the cell's SHORTER side, so the safe area never inverts. */
export const MARGIN_MAX_RATIO = 0.25;
export const ZOOM_STEP = 1.1;

export const CELL_PRESETS: ReadonlyArray<Size> = [
	{ width: 256, height: 256 },
	{ width: 512, height: 512 },
	{ width: 1024, height: 1024 }
];

function clamp(value: number, min: number, max: number): number {
	if (!Number.isFinite(value)) return min;
	return Math.min(max, Math.max(min, value));
}

function isUsable(size: Size): boolean {
	return (
		Number.isFinite(size.width) &&
		Number.isFinite(size.height) &&
		size.width > 0 &&
		size.height > 0
	);
}

/** Round and clamp one custom cell dimension; anything unparseable falls back. */
export function clampCellDimension(value: number, fallback = 512): number {
	if (!Number.isFinite(value)) return fallback;
	return clamp(Math.round(value), CELL_MIN, CELL_MAX);
}

export function maxMargin(cell: Size): number {
	if (!isUsable(cell)) return 0;
	return Math.floor(Math.min(cell.width, cell.height) * MARGIN_MAX_RATIO);
}

export function clampMargin(margin: number, cell: Size): number {
	return clamp(Math.round(margin), 0, maxMargin(cell));
}

/** The inner guide rectangle, in cell pixels. */
export function safeArea(cell: Size, margin: number): Rect {
	const m = clampMargin(margin, cell);
	return { x: m, y: m, width: cell.width - 2 * m, height: cell.height - 2 * m };
}

export function clampZoom(zoom: number): number {
	return clamp(zoom, ZOOM_MIN, ZOOM_MAX);
}

export function zoomStep(zoom: number, direction: number, factor = ZOOM_STEP): number {
	if (!Number.isFinite(direction) || direction === 0) return clampZoom(zoom);
	return clampZoom(zoom * Math.pow(factor, direction));
}

/**
 * Scale that CONTAINS the whole image inside the safe area - never `cover`.
 * The point of the margin is that nothing the user framed gets cropped away by
 * the fit itself; `cover` would push the long axis outside the safe area (and
 * outside the cell as soon as the margin is 0), which is the opposite promise.
 */
export function fitWithMargin(image: Size, cell: Size, margin: number): CropTransform {
	if (!isUsable(image) || !isUsable(cell)) {
		return { zoom: 1, offsetX: 0, offsetY: 0 };
	}
	const safe = safeArea(cell, margin);
	const zoom = Math.min(safe.width / image.width, safe.height / image.height);
	return { zoom: clampZoom(zoom), offsetX: 0, offsetY: 0 };
}

/**
 * How far the image centre may travel from the cell centre on one axis.
 *
 * One formula covers both regimes because the bound is the same magnitude
 * either way: an image LARGER than the cell may not expose a gap (its edge
 * stops at the cell edge), and an image SMALLER than the cell may not leave it
 * (its edge stops at the cell edge too).
 */
export function panLimit(drawExtent: number, cellExtent: number): number {
	if (!Number.isFinite(drawExtent) || !Number.isFinite(cellExtent)) return 0;
	return Math.abs(drawExtent - cellExtent) / 2;
}

export function clampTransform(
	transform: CropTransform,
	image: Size,
	cell: Size
): CropTransform {
	const zoom = clampZoom(transform.zoom);
	if (!isUsable(image) || !isUsable(cell)) {
		return { zoom, offsetX: 0, offsetY: 0 };
	}
	const limitX = panLimit(image.width * zoom, cell.width);
	const limitY = panLimit(image.height * zoom, cell.height);
	return {
		zoom,
		offsetX: clamp(transform.offsetX, -limitX, limitX),
		offsetY: clamp(transform.offsetY, -limitY, limitY)
	};
}

/** Where the image lands inside the cell - this is exactly what gets rasterized. */
export function computeDrawRect(image: Size, cell: Size, transform: CropTransform): Rect {
	const width = image.width * transform.zoom;
	const height = image.height * transform.zoom;
	return {
		x: (cell.width - width) / 2 + transform.offsetX,
		y: (cell.height - height) / 2 + transform.offsetY,
		width,
		height
	};
}

/** True when the drawn image sits entirely within the safe-area guide. */
export function isInsideSafeArea(rect: Rect, safe: Rect, epsilon = 1e-6): boolean {
	return (
		rect.x >= safe.x - epsilon &&
		rect.y >= safe.y - epsilon &&
		rect.x + rect.width <= safe.x + safe.width + epsilon &&
		rect.y + rect.height <= safe.y + safe.height + epsilon
	);
}

/** Display scale for the on-screen cell viewport; never magnifies past 1:1. */
export function viewportScale(cell: Size, maxPx: number): number {
	if (!isUsable(cell) || !(maxPx > 0)) return 1;
	return Math.min(1, maxPx / cell.width, maxPx / cell.height);
}
