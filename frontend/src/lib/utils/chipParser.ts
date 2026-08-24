import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { ChipData } from '$lib/types/segments';

export interface ParseChipsOptions {
	/** Enable shuffle on chips created from category-only markers (a value is picked at random per generation). */
	shuffleCategoryChips?: boolean;
}

/**
 * Parse chip patterns from text and create ChipData objects
 * Finds all #category.path or #[category path] patterns and fetches their phrasebook data
 */
export async function parseChipsFromText(
	text: string,
	options: ParseChipsOptions = {}
): Promise<Record<string, ChipData>> {
	if (!text) {
		return {};
	}

	// Find all chip patterns - supports two formats:
	// 1. Bracketed for paths with spaces: #[path with spaces]
	// 2. Simple for paths without spaces: #simplepath.subpath
	const chipPatternRegex = /#\[([^\]]+)\]|#([\w][\w.]*)/g;
	const rawMatches = [...text.matchAll(chipPatternRegex)];

	// Transform matches to extract the actual path (from either capture group)
	const matches = rawMatches.map(m => {
		const path = m[1] || m[2]; // m[1] is bracketed, m[2] is simple
		return { ...m, 1: path }; // Normalize to have path in position 1
	});

	if (matches.length === 0) return {};

	const chips: Record<string, ChipData> = {};
	const processedPaths = new Set<string>();

	// Process each match and create chip objects
	for (const match of matches) {
		const categoryPath = match[1];

		// Skip if we've already processed this path
		if (processedPaths.has(categoryPath)) {
			// Create a new chip ID for duplicate paths
			const chipId = generateChipId();
			const existingChip = Object.values(chips).find(c => c.categoryPath === categoryPath);
			if (existingChip) {
				chips[chipId] = { ...existingChip, id: chipId };
			}
			continue;
		}

		processedPaths.add(categoryPath);

		try {
			// Fetch phrasebook data for this category path
			let response = await api.searchPhrasebook(categoryPath);

			if (response.success && response.data) {
				let values = response.data.values || [];

				// If no values found, this might be a complete path like "emotions.Kindness"
				// Try fetching the parent category
				if (values.length === 0 && categoryPath.includes('.')) {
					const pathParts = categoryPath.split('.');
					const selectedValueName = pathParts.pop() ?? ''; // Get the last part (e.g., "Kindness")
					const parentPath = pathParts.join('.'); // Get parent path (e.g., "emotions")

					// Fetch parent category
					const parentResponse = await api.searchPhrasebook(parentPath);

					if (parentResponse.success && parentResponse.data) {
						const parentValues = parentResponse.data.values || [];
						if (parentValues.length > 0) {
							// Find the matching value by label or value
							let selectedValue = parentValues.find(v =>
								v.label === selectedValueName ||
								v.value === selectedValueName ||
								v.id === selectedValueName
							);

							// If not found, try case-insensitive match
							if (!selectedValue) {
								selectedValue = parentValues.find(v =>
									v.label.toLowerCase() === selectedValueName.toLowerCase() ||
									v.value.toLowerCase() === selectedValueName.toLowerCase()
								);
							}

							// If still not found, use the first value as fallback
							if (!selectedValue) {
								logger.warn('[chipParser] Could not find matching value for', selectedValueName, ', using first value');
								selectedValue = parentValues[0];
							}

							const chipId = generateChipId();

							const chipData: ChipData = {
								id: chipId,
								categoryPath: categoryPath, // Keep the full path
								valueId: selectedValue.id,
								label: selectedValue.label,
								value: selectedValue.value,
								allValues: parentValues.map(v => ({
									id: v.id,
									label: v.label,
									value: v.value
								})),
								shuffle: false,
								autoRegen: false
							};

							chips[chipId] = chipData;
							continue;
						}
					}
				}

				// If the path has values, use the first one as default
				if (values.length > 0) {
					const firstValue = values[0];
					const chipId = generateChipId();

					const chipData: ChipData = {
						id: chipId,
						categoryPath: categoryPath,
						valueId: firstValue.id,
						label: firstValue.label,
						value: firstValue.value,
						allValues: values.map(v => ({
							id: v.id,
							label: v.label,
							value: v.value
						})),
						shuffle: options.shuffleCategoryChips ?? false,
						autoRegen: false
					};

					chips[chipId] = chipData;
				} else {
					// No values found - might be an incomplete path or category
					logger.warn(`[chipParser] No values found for chip pattern: #${categoryPath}`);
				}
			}
		} catch (error) {
			logger.error(`[chipParser] Failed to fetch phrasebook data for #${categoryPath}:`, error);
		}
	}

	return chips;
}

/**
 * Generate a unique chip ID
 */
function generateChipId(): string {
	return `chip-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
}

/**
 * Parse chips from text and merge with existing chips
 * This preserves existing chip configurations while adding new ones
 */
export async function mergeChipsFromText(
	text: string,
	existingChips: Record<string, ChipData> = {}
): Promise<Record<string, ChipData>> {
	const newChips = await parseChipsFromText(text);
	return { ...existingChips, ...newChips };
}

/**
 * Hydrate a single segment with chips if it has chip markers but no chips
 * Useful for segments loaded from storage that might be missing chip data
 */
async function hydrateSegmentChips<T extends { content: string; chips?: Record<string, ChipData> }>(
	segment: T
): Promise<T> {
	// Skip if no content
	if (!segment.content) return segment;

	// Skip if segment already has chips defined
	if (segment.chips && Object.keys(segment.chips).length > 0) return segment;

	// Check if content has chip markers (both formats)
	const hasChipMarkers = /#\[[^\]]+\]|#[\w][\w.]*/.test(segment.content);
	if (!hasChipMarkers) return segment;

	// Parse and create chips
	const chips = await parseChipsFromText(segment.content);

	return {
		...segment,
		chips
	};
}

/**
 * Hydrate multiple segments with chips
 * Processes segments in parallel for better performance
 */
export async function hydrateSegments<T extends { content: string; chips?: Record<string, ChipData> }>(
	segments: T[]
): Promise<T[]> {
	return Promise.all(segments.map(segment => hydrateSegmentChips(segment)));
}
