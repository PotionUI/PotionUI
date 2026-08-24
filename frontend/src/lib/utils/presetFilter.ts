import type { PresetInfo } from '$lib/types/api';

/** The distinct engines present in a preset list, sorted for a stable chip order. */
export function availablePresetEngines(presets: PresetInfo[]): string[] {
	return Array.from(
		new Set(presets.map((p) => p.engine).filter((e): e is string => !!e))
	).sort();
}

/**
 * Narrow the preset list by an optional engine and a free-text query.
 *
 * The engine filter is an exact match; the text query matches (case-insensitively)
 * against name, category, engine, or any tag. Both are ANDed. An empty query and a
 * null engine return the list unchanged.
 */
export function filterPresets(
	presets: PresetInfo[],
	filterText: string,
	selectedEngine: string | null
): PresetInfo[] {
	const q = filterText.trim().toLowerCase();
	return presets.filter((preset) => {
		if (selectedEngine && preset.engine !== selectedEngine) return false;
		if (!q) return true;
		return (
			preset.name.toLowerCase().includes(q) ||
			!!preset.category?.toLowerCase().includes(q) ||
			!!preset.engine?.toLowerCase().includes(q) ||
			!!preset.tags?.some((tag) => tag.toLowerCase().includes(q))
		);
	});
}
