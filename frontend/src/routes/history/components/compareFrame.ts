// Pure geometry for the compare tool's overlay/wipe slider, kept out of the
// component so pointer-to-percent and keyboard-step math can be tested
// without mounting Svelte.

export type CompareMode = 'side-by-side' | 'overlay' | 'wipe';

export const COMPARE_MIN = 0;
export const COMPARE_MAX = 100;
export const COMPARE_DEFAULT = 50;
export const COMPARE_STEP = 1;
export const COMPARE_STEP_LARGE = 10;

/** Rounds and clamps a slider/handle position to the 0-100 range. */
export function clampCompareValue(value: number): number {
	if (!Number.isFinite(value)) return COMPARE_DEFAULT;
	return Math.min(COMPARE_MAX, Math.max(COMPARE_MIN, Math.round(value)));
}

/** Wipe-handle position (0-100) for a pointer at `clientX` over a frame with this bounding rect. */
export function compareValueFromPointer(clientX: number, rect: { left: number; width: number }): number {
	if (!(rect.width > 0)) return COMPARE_DEFAULT;
	return clampCompareValue(((clientX - rect.left) / rect.width) * 100);
}

const ARROW_DIRECTION: Record<string, 1 | -1> = { ArrowLeft: -1, ArrowRight: 1 };

/**
 * Next handle value for a keydown on the focused handle, or null when `key`
 * isn't one this control responds to. ArrowLeft/Right step by
 * COMPARE_STEP (COMPARE_STEP_LARGE with Shift); Home/End jump to the ends.
 */
export function compareValueFromKey(current: number, key: string, shiftKey: boolean): number | null {
	if (key === 'Home') return COMPARE_MIN;
	if (key === 'End') return COMPARE_MAX;
	const direction = ARROW_DIRECTION[key];
	if (direction === undefined) return null;
	const step = shiftKey ? COMPARE_STEP_LARGE : COMPARE_STEP;
	return clampCompareValue(current + direction * step);
}
