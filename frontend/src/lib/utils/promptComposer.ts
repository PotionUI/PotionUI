// Plain-text-first prompt composer helpers (Prompt Library "New prompt").
// Pure and side-effect free so the splitting logic is trivially unit-testable.

/**
 * Splits pasted prompt text into segment strings for the composer's opt-in
 * "Split into segments" step. Blank lines are the primary boundary (matches
 * how people paste multi-line prompt notes); if that produces only one
 * segment, falls back to splitting on every non-empty line so a single
 * newline-separated list still becomes multiple segments.
 */
export function splitPlainTextIntoSegments(text: string): string[] {
	const trimmed = text.trim();
	if (!trimmed) return [];

	const byBlankLine = trimmed
		.split(/\n\s*\n/)
		.map((part) => part.trim())
		.filter(Boolean);
	if (byBlankLine.length > 1) return byBlankLine;

	const byLine = trimmed
		.split('\n')
		.map((line) => line.trim())
		.filter(Boolean);
	return byLine.length > 0 ? byLine : [trimmed];
}
