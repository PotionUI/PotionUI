// Deterministic, low-alpha decorative tint for "no thumbnail" placeholder
// tiles, so a grid of missing-image rows reads as calm neutral tiles with a
// whisper of per-item color variation rather than identical gray boxes.
// Draws from the same colorblind-safe --viz-1..--viz-8 categorical series
// used by chipIndicatorColor.ts / vizSlot() for segment tinting -- reuses
// the djb2-ish hash idea, not the code, since this also needs a gradient
// angle out of the hash.
const VIZ_SLOTS = 8;
const ANGLES = [135, 45, -45, -135] as const;
const ALPHA = 0.12;

function hash(seed: string): number {
	let h = 5381;
	for (let i = 0; i < seed.length; i++) {
		h = (h * 33) ^ seed.charCodeAt(i);
	}
	return h >>> 0;
}

/** Seed (e.g. a label) -> deterministic `background:` CSS declaration. */
export function placeholderTint(seed: string): string {
	const h = hash(seed);
	const slot = (h % VIZ_SLOTS) + 1;
	const angle = ANGLES[Math.floor(h / VIZ_SLOTS) % ANGLES.length];
	return `background: linear-gradient(${angle}deg, rgb(var(--viz-${slot}) / ${ALPHA}), transparent 65%), rgb(var(--surface-3));`;
}
