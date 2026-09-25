export type PromptSyntaxKind = 'label' | 'marker' | 'wrap' | 'weight';
export type PromptSyntaxTone = 'signal' | 'success' | 'warning' | 'info' | 'accent' | 'danger' | 'muted';

export interface PromptSyntaxSpec {
	token: string;
	kind: PromptSyntaxKind;
	pattern?: string | null;
	insert?: string | null;
	help?: string | null;
	tone?: PromptSyntaxTone | null;
}

export interface PromptSyntaxMatch {
	start: number;
	end: number;
	text: string;
	spec: PromptSyntaxSpec;
	tone: PromptSyntaxTone;
}

export interface SyntaxTriggerMatch {
	start: number;
	end: number;
	query: string;
}

export function detectSyntaxPickerTrigger(text: string, cursorOffset: number): SyntaxTriggerMatch | null {
	let start = cursorOffset - 1;
	while (start >= 0 && text[start] !== '/' && text[start] !== '\n') {
		start--;
	}

	if (start < 0 || text[start] !== '/') return null;

	const precedingChar = start > 0 ? text[start - 1] : '';
	if (start > 0 && !/\s/.test(precedingChar)) return null;

	const query = text.substring(start + 1, cursorOffset);
	if (!/^[\w][\w .-]*$/.test(query) && !/^[\w]*$/.test(query)) return null;

	return { start, end: cursorOffset, query };
}

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function specPattern(spec: PromptSyntaxSpec): string {
	return spec.pattern ?? escapeRegExp(spec.token);
}

function kindDefaultTone(kind: PromptSyntaxKind): PromptSyntaxTone {
	if (kind === 'label') return 'info';
	if (kind === 'marker') return 'signal';
	if (kind === 'wrap') return 'signal';
	return 'muted';
}

export function syntaxToneClasses(tone: PromptSyntaxTone): string {
	if (tone === 'signal') return 'bg-signal/10 text-signal';
	if (tone === 'success') return 'bg-success/10 text-success';
	if (tone === 'warning') return 'bg-warning/10 text-warning';
	if (tone === 'info') return 'bg-info/10 text-info';
	if (tone === 'danger') return 'bg-danger/10 text-danger';
	if (tone === 'accent') return 'bg-surface-3 text-fg';
	return 'bg-surface-2 text-fg-muted';
}

export function parseWeightNumber(raw: string): number | null {
	const normalized = raw.trim().replace(',', '.');
	if (!normalized) return null;
	const value = Number(normalized);
	return Number.isFinite(value) ? value : null;
}

export function weightTone(value: number): PromptSyntaxTone {
	if (value > 1) return 'warning';
	if (value < 1) return 'info';
	return 'muted';
}

function weightValueFromMatch(match: RegExpMatchArray): number | null {
	for (let i = match.length - 1; i >= 1; i--) {
		const group = match[i];
		if (group === undefined) continue;
		const value = parseWeightNumber(group);
		if (value !== null) return value;
	}
	return null;
}

function resolveTone(spec: PromptSyntaxSpec, match: RegExpMatchArray): PromptSyntaxTone {
	if (spec.kind === 'weight') {
		const value = weightValueFromMatch(match);
		if (value !== null) return weightTone(value);
	}
	return spec.tone ?? kindDefaultTone(spec.kind);
}

export function findSyntaxMatches(text: string, specs: readonly PromptSyntaxSpec[]): PromptSyntaxMatch[] {
	if (!text || specs.length === 0) return [];

	const raw: PromptSyntaxMatch[] = [];
	for (const spec of specs) {
		let regex: RegExp;
		try {
			regex = new RegExp(specPattern(spec), 'g');
		} catch {
			continue;
		}
		let match: RegExpExecArray | null;
		while ((match = regex.exec(text)) !== null) {
			if (match[0] === '') {
				regex.lastIndex++;
				continue;
			}
			raw.push({
				start: match.index,
				end: match.index + match[0].length,
				text: match[0],
				spec,
				tone: resolveTone(spec, match)
			});
		}
	}

	raw.sort((a, b) => (a.start !== b.start ? a.start - b.start : b.end - a.end));

	const matches: PromptSyntaxMatch[] = [];
	let lastEnd = -1;
	for (const m of raw) {
		if (m.start < lastEnd) continue;
		matches.push(m);
		lastEnd = m.end;
	}
	return matches;
}

export interface SyntaxInsertResult {
	text: string;
	caretOffset: number;
	selectionStart: number | null;
	selectionEnd: number | null;
}

const PARAM_RE = /^\{([A-Za-z_][A-Za-z0-9_]*)=([^{}]*)\}/;

export function applySyntaxInsert(insert: string, selectedText: string): SyntaxInsertResult {
	let result = '';
	let caretOffset: number | null = null;
	let selectionStart: number | null = null;
	let selectionEnd: number | null = null;
	let i = 0;

	while (i < insert.length) {
		if (insert[i] === '{') {
			const paramMatch = PARAM_RE.exec(insert.slice(i));
			if (paramMatch) {
				selectionStart = result.length;
				result += paramMatch[2];
				selectionEnd = result.length;
				i += paramMatch[0].length;
				continue;
			}
			if (insert[i + 1] === '}') {
				if (selectedText === '') caretOffset = result.length;
				result += selectedText;
				i += 2;
				continue;
			}
		}
		result += insert[i];
		i++;
	}

	if (selectionStart !== null && selectionEnd !== null) {
		return { text: result, caretOffset: selectionEnd, selectionStart, selectionEnd };
	}
	return { text: result, caretOffset: caretOffset ?? result.length, selectionStart: null, selectionEnd: null };
}

export interface SyntaxTextSegment {
	text: string;
	match: PromptSyntaxMatch | null;
}

export function buildSyntaxSegments(text: string, specs: readonly PromptSyntaxSpec[]): SyntaxTextSegment[] {
	if (!text) return [];
	const matches = findSyntaxMatches(text, specs);
	if (matches.length === 0) return [{ text, match: null }];

	const segments: SyntaxTextSegment[] = [];
	let cursor = 0;
	for (const match of matches) {
		if (match.start > cursor) segments.push({ text: text.slice(cursor, match.start), match: null });
		segments.push({ text: match.text, match });
		cursor = match.end;
	}
	if (cursor < text.length) segments.push({ text: text.slice(cursor), match: null });
	return segments;
}

export function filterSyntaxSpecs(specs: readonly PromptSyntaxSpec[], query: string): PromptSyntaxSpec[] {
	const q = query.trim().toLowerCase();
	if (!q) return [...specs];
	return specs.filter(
		(spec) => spec.token.toLowerCase().includes(q) || (spec.help ?? '').toLowerCase().includes(q)
	);
}
