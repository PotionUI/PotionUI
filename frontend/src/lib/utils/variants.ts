import type { PresetModeVariant } from '$lib/types/api';

/** Returns the variant flagged `default: true`, falling back to the first
 *  variant (by `order`) if the backend ever omits the flag. `null` when the
 *  mode has no variants at all. */
export function getDefaultVariant(
	variants: PresetModeVariant[] | undefined | null
): PresetModeVariant | null {
	if (!variants || variants.length === 0) return null;
	return variants.find((v) => v.default) ?? variants[0];
}

/** Resolves which variant name should be selected for a mode: keeps
 *  `requested` if it still exists among `variants`, otherwise falls back to
 *  the mode's default variant. Returns `null` for a mode with no variants
 *  (nothing to select — the variant selector stays hidden). Non-fatal by
 *  design: a saved/requested variant that no longer exists never throws, it
 *  just falls back. */
export function resolveVariant(
	variants: PresetModeVariant[] | undefined | null,
	requested: string | null | undefined
): string | null {
	if (!variants || variants.length === 0) return null;
	if (requested && variants.some((v) => v.name === requested)) return requested;
	return getDefaultVariant(variants)?.name ?? null;
}

/** Sorted copy of `variants` by `order` (the backend already sorts, but this
 *  keeps the frontend correct even if that guarantee ever slips). */
export function sortVariants(variants: PresetModeVariant[] | undefined | null): PresetModeVariant[] {
	if (!variants) return [];
	return [...variants].sort((a, b) => a.order - b.order);
}
