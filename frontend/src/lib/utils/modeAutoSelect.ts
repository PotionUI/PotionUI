import { resolveVariant } from './variants';
import type { PresetModeVariant } from '$lib/types/api';

export interface ModeLike {
	name: string;
	variants?: PresetModeVariant[];
}

export interface ResolvedModeSelection {
	mode: string;
	variant: string | null;
}

/**
 * Picks which mode+variant a tab should land on when it has no mode selected
 * yet (a fresh preset pick) or its persisted mode no longer exists on the
 * current preset (a stale session / a mode the preset dropped). Prefers the
 * preset's declared `default_mode`, falling back to the first available mode.
 * Returns `null` when the preset has no modes at all - nothing to select.
 *
 * Mirrors the admin-preview pattern in previewGeneration.ts
 * (`defaultMode || modes[0].name`) so both surfaces land a fresh user on a
 * usable mode instead of an empty selector.
 */
export function resolveDefaultModeSelection(
	modes: ModeLike[],
	defaultMode: string | null | undefined
): ResolvedModeSelection | null {
	if (!modes.length) return null;
	const fallbackName = defaultMode && modes.some((m) => m.name === defaultMode) ? defaultMode : modes[0].name;
	const modeInfo = modes.find((m) => m.name === fallbackName);
	return {
		mode: fallbackName,
		variant: resolveVariant(modeInfo?.variants, null)
	};
}
