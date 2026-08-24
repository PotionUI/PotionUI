import type { PresetRequirements } from '$lib/types/api';

/** `12` -> `"12"`, `12.5` -> `"12.5"` - avoids a trailing `.0` on the common whole-GB case. */
function formatGb(value: number): string {
	return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * Compact VRAM badge text for a preset's `requires:` block. Prefers the
 * minimum (a functional claim); falls back to the recommended figure when
 * only that was measured (see docs/user/hardware-requirements.md). `null`
 * when neither is set.
 */
export function formatVramBadge(requires?: PresetRequirements | null): string | null {
	if (!requires) return null;
	if (requires.min_vram_gb != null) return `${formatGb(requires.min_vram_gb)} GB VRAM`;
	if (requires.recommended_vram_gb != null) {
		return `${formatGb(requires.recommended_vram_gb)}+ GB VRAM recommended`;
	}
	return null;
}

/** Compact system-RAM badge text, or `null` when `requires.min_ram_gb` is unset. */
export function formatRamBadge(requires?: PresetRequirements | null): string | null {
	if (requires?.min_ram_gb == null) return null;
	return `${formatGb(requires.min_ram_gb)} GB RAM`;
}

/**
 * Non-blocking warning copy for when the detected GPU has less VRAM than a
 * preset's declared minimum. `null` when there's nothing to warn about -
 * either the preset declares no minimum, or the detected VRAM is unknown, or
 * it meets the minimum.
 */
export function vramShortfall(
	requires?: PresetRequirements | null,
	detectedVramGb?: number | null
): string | null {
	if (requires?.min_vram_gb == null || detectedVramGb == null) return null;
	if (detectedVramGb >= requires.min_vram_gb) return null;
	return `needs ${formatGb(requires.min_vram_gb)} GB — this machine has ${formatGb(detectedVramGb)} GB`;
}
