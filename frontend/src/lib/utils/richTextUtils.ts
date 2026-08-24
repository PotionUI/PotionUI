import type { ChipData } from '$lib/types/segments';

/**
 * Encodes a category path for storage in text
 * Paths with spaces use bracket format: #[path with spaces]
 * Simple paths use plain format: #simplepath
 */
function encodePathForText(path: string): string {
	if (path.includes(' ')) {
		return `#[${path}]`;
	}
	return `#${path}`;
}

/**
 * Converts rich text with chips to plain text for API submission
 * Replaces chip placeholders (#categoryPath or #[category path]) with actual values
 */
export function richTextToPlainText(value: string, chips: Record<string, ChipData>): string {
	if (!value || !chips || Object.keys(chips).length === 0) return value;

	// Build array of replacements in order of appearance in text
	const replacements: Array<{ position: number; length: number; value: string }> = [];

	Object.entries(chips).forEach(([id, chip]) => {
		// Try both formats: bracketed for paths with spaces, plain for simple paths
		const bracketedPattern = `#[${chip.categoryPath}]`;
		const plainPattern = `#${chip.categoryPath}`;

		// First try the appropriate format based on whether path has spaces
		const primaryPattern = chip.categoryPath.includes(' ') ? bracketedPattern : plainPattern;
		const secondaryPattern = chip.categoryPath.includes(' ') ? plainPattern : bracketedPattern;

		let searchPos = 0;
		let occurrence = 0;
		let pattern = primaryPattern;
		let pos = value.indexOf(pattern, searchPos);

		// If primary format not found, try secondary (for backwards compatibility)
		if (pos === -1) {
			pattern = secondaryPattern;
			pos = value.indexOf(pattern, searchPos);
		}

		// Find all occurrences of this category path
		while (pos !== -1) {
			// Check if this occurrence matches this chip
			// Count how many chips with same path came before
			let samePaths = 0;
			Object.entries(chips).forEach(([cid, c]) => {
				if (c.categoryPath === chip.categoryPath && cid !== id) {
					const cPattern = encodePathForText(c.categoryPath);
					const cPos = value.indexOf(cPattern);
					if (cPos !== -1 && cPos < pos) samePaths++;
				}
			});

			if (samePaths === occurrence) {
				replacements.push({
					position: pos,
					length: pattern.length,
					value: chip.value
				});
				break;
			}

			occurrence++;
			searchPos = pos + pattern.length;
			pos = value.indexOf(pattern, searchPos);
		}
	});

	// Sort replacements by position (descending) to replace from end to start
	// This prevents position shifts from affecting later replacements
	replacements.sort((a, b) => b.position - a.position);

	// Apply replacements
	let result = value;
	replacements.forEach(({ position, length, value }) => {
		result = result.substring(0, position) + value + result.substring(position + length);
	});

	return result;
}

/**
 * Regenerates chips with shuffle enabled by picking new random values
 * Called when generation starts to auto-shuffle enabled chips
 */
export function regenerateAutoChips(chips: Record<string, ChipData>): Record<string, ChipData> {
	const updatedChips: Record<string, ChipData> = {};
	let hasChanges = false;

	Object.entries(chips).forEach(([chipId, chipData]) => {
		// Check if shuffle is enabled (the toggle in the UI)
		if (chipData.shuffle && chipData.allValues.length > 1) {
			// Pick a random value different from current
			const availableValues = chipData.allValues.filter((v) => v.id !== chipData.valueId);
			if (availableValues.length > 0) {
				const randomValue = availableValues[Math.floor(Math.random() * availableValues.length)];
				updatedChips[chipId] = {
					...chipData,
					valueId: randomValue.id,
					label: randomValue.label,
					value: randomValue.value
				};
				hasChanges = true;
				return;
			}
		}
		updatedChips[chipId] = chipData;
	});

	return hasChanges ? updatedChips : chips;
}
