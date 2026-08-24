/**
 * Which image an inpaint mask belongs to.
 *
 * The mask a user paints in InpaintingModal is uploaded separately and lands on
 * the `${name}_inpaint_mask` sibling channel, not on the field's value. Nothing
 * tied the two together, so swapping the image out (a new upload, a pick from
 * history or the upload library, a clear) left the old mask attached and the
 * next generation was masked with the previous image's shape.
 */

/**
 * Stable identity of the image a mask was painted on.
 *
 * The stored path, never the `url`: an upload's `url` is a fresh
 * `URL.createObjectURL` blob handle minted per upload, so it changes even when
 * the same file is re-selected, and it is absent on some value shapes. `url` is
 * only a last resort for values that carry nothing else.
 */
export function maskSubjectKey(value: unknown): string | null {
	if (!value) return null;
	if (typeof value === 'string') return value || null;
	if (typeof value !== 'object') return null;

	const v = value as Record<string, unknown>;
	for (const key of ['relative_path', 'path', 'url'] as const) {
		const candidate = v[key];
		if (typeof candidate === 'string' && candidate) return candidate;
	}
	return null;
}

/**
 * True when the field's current value is a DIFFERENT image than the one the
 * held mask was painted on - including "no image at all" after a clear.
 *
 * `maskSubject` is null when no mask is held, in which case there is nothing to
 * clear and this is always false.
 */
export function shouldClearMask(maskSubject: string | null, value: unknown): boolean {
	if (maskSubject === null) return false;
	return maskSubjectKey(value) !== maskSubject;
}
