/**
 * The crop rectangle, in units that survive a resize of the stage.
 *
 * Everything here is NORMALISED against the media as it is currently
 * displayed: `{x, y, width, height}` all live in 0..1. The stage the user drags
 * on is whatever size the viewport allows and changes when the window does, so
 * a rectangle stored in stage pixels is a rectangle that moves when nothing
 * moved it.
 *
 * "Currently displayed" matters: a rotate or a flip is applied BEFORE the crop
 * (see `planToOperations`), so the rectangle is always in the coordinates the
 * user drew it in - which is the whole point of letting them rotate first.
 *
 * The clamping here mirrors `_validated_crop` in
 * `src/features/media/editing/operations.py`. The server is still the
 * authority; this exists so the UI cannot offer a rectangle the server will
 * refuse, not so the server can stop checking.
 */

/** A rectangle over the displayed media, every field in 0..1. */
export interface CropRect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export type CropCorner = 'nw' | 'ne' | 'sw' | 'se';

/**
 * The smallest fraction of a side a drag may leave. Not a pixel floor - that
 * is applied in `toCropOperation`, where the source size is known - but a
 * usability one: a rectangle smaller than this cannot be grabbed again.
 */
export const MIN_CROP_FRACTION = 0.02;

export const FULL_CROP: CropRect = { x: 0, y: 0, width: 1, height: 1 };

export interface CropAspect {
	key: string;
	label: string;
	/** Width ÷ height in PIXELS, or null for a free rectangle. */
	ratio: number | null;
}

export const CROP_ASPECTS: readonly CropAspect[] = [
	{ key: 'free', label: 'FREE', ratio: null },
	{ key: '1:1', label: '1:1', ratio: 1 },
	{ key: '16:9', label: '16:9', ratio: 16 / 9 },
	{ key: '9:16', label: '9:16', ratio: 9 / 16 },
	{ key: '4:3', label: '4:3', ratio: 4 / 3 },
	{ key: '3:4', label: '3:4', ratio: 3 / 4 }
];

function clamp(value: number, low: number, high: number): number {
	if (!Number.isFinite(value)) return low;
	return Math.min(high, Math.max(low, value));
}

/** A rectangle inside the frame, never smaller than `MIN_CROP_FRACTION`. */
export function clampCropRect(rect: CropRect): CropRect {
	const width = clamp(rect.width, MIN_CROP_FRACTION, 1);
	const height = clamp(rect.height, MIN_CROP_FRACTION, 1);
	return {
		x: clamp(rect.x, 0, 1 - width),
		y: clamp(rect.y, 0, 1 - height),
		width,
		height
	};
}

/** Translate the rectangle, keeping its size and staying inside the frame. */
export function moveCropRect(rect: CropRect, dx: number, dy: number): CropRect {
	return clampCropRect({ ...rect, x: rect.x + dx, y: rect.y + dy });
}

export interface ResizeOptions {
	/** Locked pixel aspect (width ÷ height), or null while the ratio is free. */
	ratio: number | null;
	/** The displayed media's own pixel aspect (width ÷ height). */
	stageAspect: number;
}

/**
 * Drag one corner. `start` is the rectangle as it was when the pointer went
 * down, `dx`/`dy` the pointer's travel since, normalised against the stage.
 *
 * Under a locked ratio only the width is taken from the pointer and the height
 * is derived: driving both from the pointer and then correcting one of them
 * makes the rectangle creep away from the cursor over a long drag.
 */
export function resizeCropRect(
	start: CropRect,
	corner: CropCorner,
	dx: number,
	dy: number,
	options: ResizeOptions
): CropRect {
	let { x, y, width, height } = start;

	if (corner.includes('e')) width = start.width + dx;
	if (corner.includes('w')) {
		width = start.width - dx;
		x = start.x + dx;
	}
	if (corner.includes('s')) height = start.height + dy;
	if (corner.includes('n')) {
		height = start.height - dy;
		y = start.y + dy;
	}

	width = Math.max(MIN_CROP_FRACTION, width);
	height = Math.max(MIN_CROP_FRACTION, height);

	if (options.ratio && options.stageAspect > 0) {
		height = Math.max(MIN_CROP_FRACTION, width / normalisedRatio(options.ratio, options.stageAspect));
		// A north handle grows upward: the south edge is the one that must not
		// move, so the top is re-derived from it rather than left where the
		// pointer put it.
		if (corner.includes('n')) y = start.y + start.height - height;
	}

	// The edge the drag did NOT move is the anchor - a rectangle that ran out
	// of frame must shrink, never slide the anchored edge along with it.
	if (corner.includes('w')) {
		const right = start.x + start.width;
		x = clamp(x, 0, right - MIN_CROP_FRACTION);
		width = Math.min(width, right - x);
	} else {
		width = Math.min(width, 1 - x);
	}

	if (corner.includes('n')) {
		const bottom = start.y + start.height;
		y = clamp(y, 0, bottom - MIN_CROP_FRACTION);
		height = Math.min(height, bottom - y);
	} else {
		height = Math.min(height, 1 - y);
	}

	return clampCropRect({ x, y, width, height });
}

/**
 * A pixel ratio expressed in normalised units. A 1:1 pixel crop of a 2:1 image
 * is a 0.5:1 rectangle in normalised space; forgetting this conversion is how
 * an aspect preset comes out square-ish instead of square.
 */
export function normalisedRatio(pixelRatio: number, stageAspect: number): number {
	return pixelRatio / stageAspect;
}

/**
 * The largest rectangle of `ratio` that fits the frame, centred on where the
 * current rectangle was. Centring rather than resetting keeps the subject the
 * user framed under the new shape.
 */
export function fitAspect(rect: CropRect, pixelRatio: number, stageAspect: number): CropRect {
	if (!(stageAspect > 0) || !(pixelRatio > 0)) return clampCropRect(rect);

	const ratio = normalisedRatio(pixelRatio, stageAspect);
	let width = 1;
	let height = 1 / ratio;
	if (height > 1) {
		height = 1;
		width = ratio;
	}

	const centreX = rect.x + rect.width / 2;
	const centreY = rect.y + rect.height / 2;
	return clampCropRect({
		x: centreX - width / 2,
		y: centreY - height / 2,
		width,
		height
	});
}

/** True when the rectangle covers the whole frame, so no crop need be sent. */
export function isFullFrame(rect: CropRect, tolerance = 0.001): boolean {
	return (
		rect.x <= tolerance &&
		rect.y <= tolerance &&
		rect.width >= 1 - tolerance &&
		rect.height >= 1 - tolerance
	);
}

export interface PixelSize {
	width: number;
	height: number;
}

/**
 * The size the crop produces, in source pixels. Reported to the user before
 * they commit, so it must agree with what `toCropOperation` will actually ask
 * for - both round the same way for that reason.
 */
export function cropOutputSize(rect: CropRect, source: PixelSize): PixelSize {
	const operation = toCropOperation(rect, source);
	return { width: operation.width, height: operation.height };
}

export interface CropOperationPayload {
	type: 'crop';
	x: number;
	y: number;
	width: number;
	height: number;
}

/**
 * The rectangle as the `crop` operation the API takes: integer pixels from the
 * top-left of the displayed media.
 *
 * Rounding a normalised rectangle can put `x + width` a pixel past the edge,
 * which the server refuses outright - so the origin is clamped into the frame
 * first and the size to what is left of it. At least one pixel of each side
 * survives; a zero-sized crop is also a refusal.
 */
export function toCropOperation(rect: CropRect, source: PixelSize): CropOperationPayload {
	const sourceWidth = Math.max(1, Math.floor(source.width));
	const sourceHeight = Math.max(1, Math.floor(source.height));
	const safe = clampCropRect(rect);

	const x = clamp(Math.round(safe.x * sourceWidth), 0, sourceWidth - 1);
	const y = clamp(Math.round(safe.y * sourceHeight), 0, sourceHeight - 1);
	const width = clamp(Math.round(safe.width * sourceWidth), 1, sourceWidth - x);
	const height = clamp(Math.round(safe.height * sourceHeight), 1, sourceHeight - y);

	return { type: 'crop', x, y, width, height };
}

export type Rotation = 0 | 90 | 180 | 270;

/** The media's size as displayed, which a quarter turn transposes. */
export function displaySize(source: PixelSize, rotation: Rotation): PixelSize {
	if (rotation === 90 || rotation === 270) {
		return { width: source.height, height: source.width };
	}
	return { width: source.width, height: source.height };
}

/** Quarter turns stay inside 0/90/180/270 however many times they are pressed. */
export function turn(rotation: Rotation, quarters: number): Rotation {
	const next = (((rotation + quarters * 90) % 360) + 360) % 360;
	return next as Rotation;
}

/**
 * Carry the rectangle through a quarter turn of the frame it is drawn on.
 *
 * Without this, rotating leaves the rectangle sitting at the same normalised
 * coordinates of a frame that has transposed under it - so the selection jumps
 * to a different part of the picture, and on a non-square image changes shape
 * as well.
 */
export function rotateCropRect(rect: CropRect, quarters: number): CropRect {
	let next = clampCropRect(rect);
	const turns = (((Math.round(quarters) % 4) + 4) % 4);
	for (let i = 0; i < turns; i += 1) {
		next = clampCropRect({
			x: 1 - (next.y + next.height),
			y: next.x,
			width: next.height,
			height: next.width
		});
	}
	return next;
}

/** Mirror the rectangle with the frame, so it keeps covering the same content. */
export function mirrorCropRect(rect: CropRect, axis: 'horizontal' | 'vertical'): CropRect {
	const safe = clampCropRect(rect);
	if (axis === 'horizontal') {
		return clampCropRect({ ...safe, x: 1 - (safe.x + safe.width) });
	}
	return clampCropRect({ ...safe, y: 1 - (safe.y + safe.height) });
}
