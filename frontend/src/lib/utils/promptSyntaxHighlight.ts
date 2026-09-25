import type { PromptSyntaxTone } from './promptSyntax';

const TONES: PromptSyntaxTone[] = ['signal', 'success', 'warning', 'info', 'accent', 'danger', 'muted'];

export interface SyntaxHighlightSpan {
	node: Text;
	start: number;
	end: number;
}

export interface SyntaxHighlightRangeInput {
	start: number;
	end: number;
	tone: PromptSyntaxTone;
}

export function buildSyntaxHighlightRanges(
	matches: readonly SyntaxHighlightRangeInput[],
	spans: readonly SyntaxHighlightSpan[]
): Map<PromptSyntaxTone, Range[]> {
	const rangesByTone = new Map<PromptSyntaxTone, Range[]>();
	for (const m of matches) {
		const startSpan = spans.find((s) => m.start >= s.start && m.start < s.end);
		const endSpan = spans.find((s) => m.end > s.start && m.end <= s.end);
		if (!startSpan || !endSpan) continue;
		const range = document.createRange();
		range.setStart(startSpan.node, m.start - startSpan.start);
		range.setEnd(endSpan.node, m.end - endSpan.start);
		const existing = rangesByTone.get(m.tone) ?? [];
		existing.push(range);
		rangesByTone.set(m.tone, existing);
	}
	return rangesByTone;
}

export function syntaxHighlightName(tone: PromptSyntaxTone): string {
	return `potionui-syntax-${tone}`;
}

let sharedHighlights: Map<PromptSyntaxTone, Highlight> | null = null;
const rangesByOwner = new Map<symbol, Map<PromptSyntaxTone, Range[]>>();

function isSyntaxHighlightSupported(): boolean {
	return (
		typeof window !== 'undefined' &&
		typeof Highlight !== 'undefined' &&
		typeof CSS !== 'undefined' &&
		!!CSS.highlights
	);
}

function ensureHighlights(): Map<PromptSyntaxTone, Highlight> | null {
	if (!isSyntaxHighlightSupported()) return null;
	if (!sharedHighlights) {
		sharedHighlights = new Map();
		for (const tone of TONES) {
			const highlight = new Highlight();
			sharedHighlights.set(tone, highlight);
			CSS.highlights.set(syntaxHighlightName(tone), highlight);
		}
	}
	return sharedHighlights;
}

export function setOwnerSyntaxHighlightRanges(owner: symbol, rangesByTone: Map<PromptSyntaxTone, Range[]>): void {
	const highlights = ensureHighlights();
	if (!highlights) return;

	const previous = rangesByOwner.get(owner);
	if (previous) {
		for (const [tone, ranges] of previous) {
			const highlight = highlights.get(tone);
			if (highlight) for (const range of ranges) highlight.delete(range);
		}
	}

	if (rangesByTone.size > 0) {
		rangesByOwner.set(owner, rangesByTone);
	} else {
		rangesByOwner.delete(owner);
	}

	for (const [tone, ranges] of rangesByTone) {
		const highlight = highlights.get(tone);
		if (!highlight) continue;
		for (const range of ranges) highlight.add(range);
	}
}

export function clearOwnerSyntaxHighlightRanges(owner: symbol): void {
	setOwnerSyntaxHighlightRanges(owner, new Map());
}
