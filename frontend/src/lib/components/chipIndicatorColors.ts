/**
 * Deliberately raw palette hues, not semantic tokens: an indicator dot answers
 * "which option was picked", so the colors have to stay mutually
 * distinguishable and must NOT read as state (signal/success/warning already
 * mean something else in this UI).
 */
export const CHIP_INDICATOR_COLORS = [
	'bg-red-400', 'bg-orange-400', 'bg-amber-400', 'bg-yellow-400', 'bg-lime-400',
	'bg-green-400', 'bg-emerald-400', 'bg-teal-400', 'bg-cyan-400', 'bg-sky-400',
	'bg-blue-400', 'bg-indigo-400', 'bg-violet-400', 'bg-fuchsia-400', 'bg-pink-400', 'bg-rose-400'
];

/** For chips that have a natural position in a list (a choice group's index). */
export function chipIndicatorColorAt(index: number): string {
	return CHIP_INDICATOR_COLORS[index % CHIP_INDICATOR_COLORS.length];
}

/**
 * For chips with no list position — a `${name}` usage can appear anywhere in the
 * prompt, so the color has to be derived from the name to stay stable across
 * re-renders and across every occurrence of the same variable.
 */
export function chipIndicatorColorForName(name: string): string {
	let hash = 0;
	for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
	return CHIP_INDICATOR_COLORS[hash % CHIP_INDICATOR_COLORS.length];
}
