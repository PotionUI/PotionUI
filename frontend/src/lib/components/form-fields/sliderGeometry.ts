/**
 * Geometry for SliderField's custom range track, kept outside the component so
 * it can be tested (vitest runs `environment: 'node'`, no DOM) - same reason as
 * gateState.ts and sectionState.ts.
 *
 * A native range thumb travels within `width - THUMB_PX` and is centred on its
 * position, so anything meant to line up with the thumb - the gradient fill's
 * stop, the default-value tick - has to use the same inset. Placing them at a
 * plain `fraction%` of the full width makes them agree with the thumb only at
 * 50% and drift by up to half a thumb at the ends.
 */

/** Must match the ::-webkit-slider-thumb / ::-moz-range-thumb width. */
export const THUMB_PX = 14;

/** Clamp a raw value to 0..1 along min..max; 0 when the range is degenerate. */
export function trackFraction(value: number, min: number, max: number): number {
	const range = max - min;
	if (!(range > 0) || !Number.isFinite(value)) return 0;
	return Math.min(1, Math.max(0, (value - min) / range));
}

/** CSS length for the thumb centre at a 0..1 position along the track. */
export function trackOffset(fraction: number): string {
	const clamped = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0));
	return `calc(${THUMB_PX / 2}px + ${clamped} * (100% - ${THUMB_PX}px))`;
}
