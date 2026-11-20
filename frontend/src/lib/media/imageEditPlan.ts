/**
 * What the crop editor is holding, and the operation list it turns into.
 *
 * ORDER IS THE WHOLE POINT. The rectangle the user dragged is in the
 * coordinates of what they were looking at, so every orientation change has to
 * be applied BEFORE the crop - `[rotate, flip, crop, resize]`. Emitting the
 * crop first would apply their rectangle to the un-rotated frame and silently
 * cut a different part of the picture.
 *
 * A plan that changes nothing produces an empty list, which the server refuses
 * ("No operations were given") - so `planIsNoop` is what the Apply button is
 * disabled on rather than a guess about which controls were touched.
 */

import {
	FULL_CROP,
	cropOutputSize,
	displaySize,
	isFullFrame,
	toCropOperation,
	type CropRect,
	type PixelSize,
	type Rotation
} from './cropGeometry';
import type { EditOperation } from './editOperations';

export interface ImageEditPlan {
	rotation: Rotation;
	flipHorizontal: boolean;
	flipVertical: boolean;
	/** Normalised over the media AS DISPLAYED, i.e. after the rotation. */
	rect: CropRect;
	/**
	 * Longest side of the output, or null to keep the cropped size. Only one
	 * side is ever sent: the API fills the other in from the aspect ratio, and
	 * sending both would let a rounding difference squash the picture.
	 */
	targetLongestSide: number | null;
}

export const EMPTY_IMAGE_PLAN: ImageEditPlan = {
	rotation: 0,
	flipHorizontal: false,
	flipVertical: false,
	rect: FULL_CROP,
	targetLongestSide: null
};

/** The frame the crop rectangle is drawn on: the source, after the rotation. */
export function planDisplaySize(plan: ImageEditPlan, source: PixelSize): PixelSize {
	return displaySize(source, plan.rotation);
}

/** The size the plan produces, in pixels. */
export function planOutputSize(plan: ImageEditPlan, source: PixelSize): PixelSize {
	const cropped = cropOutputSize(plan.rect, planDisplaySize(plan, source));
	const longest = Math.max(cropped.width, cropped.height);
	if (!plan.targetLongestSide || plan.targetLongestSide === longest || longest === 0) {
		return cropped;
	}
	const scale = plan.targetLongestSide / longest;
	return {
		width: Math.max(1, Math.round(cropped.width * scale)),
		height: Math.max(1, Math.round(cropped.height * scale))
	};
}

/** True when the plan would produce a byte-for-byte re-encode of the source. */
export function planIsNoop(plan: ImageEditPlan, source: PixelSize): boolean {
	return planToOperations(plan, source).length === 0;
}

export function planToOperations(plan: ImageEditPlan, source: PixelSize): EditOperation[] {
	const operations: EditOperation[] = [];

	if (plan.rotation !== 0) {
		operations.push({ type: 'rotate', degrees: plan.rotation });
	}
	if (plan.flipHorizontal) {
		operations.push({ type: 'flip', axis: 'horizontal' });
	}
	if (plan.flipVertical) {
		operations.push({ type: 'flip', axis: 'vertical' });
	}

	const frame = planDisplaySize(plan, source);
	if (!isFullFrame(plan.rect)) {
		operations.push(toCropOperation(plan.rect, frame));
	}

	const cropped = cropOutputSize(plan.rect, frame);
	const longest = Math.max(cropped.width, cropped.height);
	if (plan.targetLongestSide && longest > 0 && plan.targetLongestSide !== longest) {
		operations.push(
			cropped.width >= cropped.height
				? { type: 'resize', width: plan.targetLongestSide }
				: { type: 'resize', height: plan.targetLongestSide }
		);
	}

	return operations;
}
