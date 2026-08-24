import type { ChipData, Segment } from '$lib/types/segments';
import { flattenRichSegments, type SegmentJoin } from '$lib/utils/richSegments';
import { parseChipsFromText } from '$lib/utils/chipParser';
import { logger } from '$lib/utils/logger';

/** Resolve enabled segments; collapsed state is presentation-only. */
export function resolvePromptSegments(segments: Segment[] = [], join: SegmentJoin = 'comma'): string {
	return flattenRichSegments(segments, join);
}

/**
 * Locates the segment an LLM-proposed `<tool_action type="update_segment">`
 * targets: match by id first (segments can be reordered between the tool
 * call and the user clicking Apply), falling back to the index the LLM saw.
 * Returns -1 if neither resolves — callers should treat that as a no-op
 * rather than writing to the wrong segment.
 */
export function locateSegmentIndex(
	segments: Segment[],
	target: { segmentId: string; segmentIndex: number }
): number {
	const byId = segments.findIndex((s) => s.id === target.segmentId);
	if (byId !== -1) return byId;
	if (target.segmentIndex >= 0 && target.segmentIndex < segments.length) return target.segmentIndex;
	return -1;
}

/**
 * Re-applies the user's prior chip choices (value/shuffle/autoRegen) onto
 * freshly re-parsed chips for `#category.path` markers the LLM kept
 * unchanged, matched by category path. Each old chip is consumed at most
 * once so duplicate markers of the same category don't all inherit the same
 * prior value. Markers the LLM introduced (no category match) keep the
 * freshly parsed chip as-is.
 */
export function mergeChipSelections(
	oldChips: Record<string, ChipData>,
	newChips: Record<string, ChipData>
): Record<string, ChipData> {
	const merged = { ...newChips };
	const consumed = new Set<string>();
	for (const [chipId, chip] of Object.entries(merged)) {
		const match = Object.entries(oldChips).find(
			([oldId, old]) => !consumed.has(oldId) && old.categoryPath === chip.categoryPath
		);
		if (match) {
			consumed.add(match[0]);
			const old = match[1];
			merged[chipId] = {
				...chip,
				valueId: old.valueId,
				label: old.label,
				value: old.value,
				shuffle: old.shuffle,
				autoRegen: old.autoRegen
			};
		}
	}
	return merged;
}

/**
 * Applies one LLM-proposed segment update — resolve, hydrate `#category.path`
 * markers into chips (carrying forward the user's prior chip choices for
 * markers the LLM kept), write the new content — the shared core behind both
 * the legacy `<tool_action type="update_segment">` card apply and the
 * `update_segment` tool's `apply_segment_updates` result. Returns null when
 * the target segment can't be resolved.
 */
export async function applySegmentUpdate(
	sourceSegments: Segment[],
	target: { segmentId: string; segmentIndex: number; content: string }
): Promise<{ segments: Segment[]; index: number } | null> {
	const idx = locateSegmentIndex(sourceSegments, target);
	if (idx === -1) return null;

	let newChips: Record<string, ChipData> = {};
	try {
		newChips = await parseChipsFromText(target.content, { shuffleCategoryChips: true });
	} catch (err) {
		logger.error('Failed to parse chips from applied segment content:', err);
	}

	const mergedChips = mergeChipSelections(sourceSegments[idx].chips || {}, newChips);
	const segments = [...sourceSegments];
	segments[idx] = { ...segments[idx], content: target.content, chips: mergedChips };
	return { segments, index: idx };
}
