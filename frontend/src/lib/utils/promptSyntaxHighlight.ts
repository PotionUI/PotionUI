import { sanitizeSyntaxColor, type PromptSyntaxTone } from './promptSyntax';

const TONES: PromptSyntaxTone[] = ['signal', 'success', 'warning', 'info', 'accent', 'danger', 'muted'];
const COLOR_HIGHLIGHT_ALPHA = '16%';

export interface SyntaxHighlightSpan {
	node: Text;
	start: number;
	end: number;
}

export interface SyntaxHighlightRangeInput {
	start: number;
	end: number;
	tone: PromptSyntaxTone;
	color?: string | null;
}

export interface SyntaxHighlightRanges {
	tone: Map<PromptSyntaxTone, Range[]>;
	color: Map<string, Range[]>;
}

export function buildSyntaxHighlightRanges(
	matches: readonly SyntaxHighlightRangeInput[],
	spans: readonly SyntaxHighlightSpan[]
): SyntaxHighlightRanges {
	const toneRanges = new Map<PromptSyntaxTone, Range[]>();
	const colorRanges = new Map<string, Range[]>();
	for (const m of matches) {
		const startSpan = spans.find((s) => m.start >= s.start && m.start < s.end);
		const endSpan = spans.find((s) => m.end > s.start && m.end <= s.end);
		if (!startSpan || !endSpan) continue;
		const range = document.createRange();
		range.setStart(startSpan.node, m.start - startSpan.start);
		range.setEnd(endSpan.node, m.end - endSpan.start);

		const color = sanitizeSyntaxColor(m.color);
		if (color) {
			const existing = colorRanges.get(color) ?? [];
			existing.push(range);
			colorRanges.set(color, existing);
		} else {
			const existing = toneRanges.get(m.tone) ?? [];
			existing.push(range);
			toneRanges.set(m.tone, existing);
		}
	}
	return { tone: toneRanges, color: colorRanges };
}

export function syntaxHighlightName(tone: PromptSyntaxTone): string {
	return `potionui-syntax-${tone}`;
}

export function syntaxColorHighlightName(color: string): string | null {
	const sanitized = sanitizeSyntaxColor(color);
	if (!sanitized) return null;
	return `potionui-syntax-c-${sanitized.replace('#', '')}`;
}

let sharedToneHighlights: Map<PromptSyntaxTone, Highlight> | null = null;
const colorHighlights = new Map<string, Highlight>();
const rangesByOwner = new Map<symbol, SyntaxHighlightRanges>();
let colorStyleEl: HTMLStyleElement | null = null;

function isSyntaxHighlightSupported(): boolean {
	return (
		typeof window !== 'undefined' &&
		typeof Highlight !== 'undefined' &&
		typeof CSS !== 'undefined' &&
		!!CSS.highlights
	);
}

function ensureToneHighlights(): Map<PromptSyntaxTone, Highlight> | null {
	if (!isSyntaxHighlightSupported()) return null;
	if (!sharedToneHighlights) {
		sharedToneHighlights = new Map();
		for (const tone of TONES) {
			const highlight = new Highlight();
			sharedToneHighlights.set(tone, highlight);
			CSS.highlights.set(syntaxHighlightName(tone), highlight);
		}
	}
	return sharedToneHighlights;
}

function ensureColorStyleElement(): HTMLStyleElement | null {
	if (typeof document === 'undefined') return null;
	if (!colorStyleEl) {
		colorStyleEl = document.createElement('style');
		colorStyleEl.setAttribute('data-potionui-syntax-colors', '');
		document.head.appendChild(colorStyleEl);
	}
	return colorStyleEl;
}

function ensureColorHighlight(color: string): Highlight | null {
	if (!isSyntaxHighlightSupported()) return null;
	const sanitized = sanitizeSyntaxColor(color);
	if (!sanitized) return null;

	let highlight = colorHighlights.get(sanitized);
	if (highlight) return highlight;

	highlight = new Highlight();
	colorHighlights.set(sanitized, highlight);
	const name = syntaxColorHighlightName(sanitized);
	if (name) {
		CSS.highlights.set(name, highlight);
		const styleEl = ensureColorStyleElement();
		try {
			styleEl?.sheet?.insertRule(
				`::highlight(${name}) { background-color: color-mix(in srgb, ${sanitized} ${COLOR_HIGHLIGHT_ALPHA}, transparent); color: ${sanitized}; }`,
				styleEl.sheet.cssRules.length
			);
		} catch {
		}
	}
	return highlight;
}

export function setOwnerSyntaxHighlightRanges(owner: symbol, ranges: SyntaxHighlightRanges): void {
	const toneHighlights = ensureToneHighlights();
	if (!toneHighlights) return;

	const previous = rangesByOwner.get(owner);
	if (previous) {
		for (const [tone, prevRanges] of previous.tone) {
			const highlight = toneHighlights.get(tone);
			if (highlight) for (const range of prevRanges) highlight.delete(range);
		}
		for (const [color, prevRanges] of previous.color) {
			const highlight = colorHighlights.get(color);
			if (highlight) for (const range of prevRanges) highlight.delete(range);
		}
	}

	if (ranges.tone.size > 0 || ranges.color.size > 0) {
		rangesByOwner.set(owner, ranges);
	} else {
		rangesByOwner.delete(owner);
	}

	for (const [tone, toneRanges] of ranges.tone) {
		const highlight = toneHighlights.get(tone);
		if (!highlight) continue;
		for (const range of toneRanges) highlight.add(range);
	}
	for (const [color, colorRanges] of ranges.color) {
		const highlight = ensureColorHighlight(color);
		if (!highlight) continue;
		for (const range of colorRanges) highlight.add(range);
	}
}

export function clearOwnerSyntaxHighlightRanges(owner: symbol): void {
	setOwnerSyntaxHighlightRanges(owner, { tone: new Map(), color: new Map() });
}
