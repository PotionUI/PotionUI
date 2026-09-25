// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { buildSyntaxHighlightRanges, syntaxColorHighlightName, type SyntaxHighlightSpan } from './promptSyntaxHighlight';

function textNode(text: string): Text {
	return document.createTextNode(text);
}

describe('buildSyntaxHighlightRanges', () => {
	it('builds one collapsed-to-node range when the match fits in a single span', () => {
		const node = textNode('(S1) says hello');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: 15 }];

		const ranges = buildSyntaxHighlightRanges([{ start: 0, end: 4, tone: 'accent' }], spans);

		expect(ranges.tone.get('accent')).toHaveLength(1);
		const range = ranges.tone.get('accent')![0];
		expect(range.startContainer).toBe(node);
		expect(range.startOffset).toBe(0);
		expect(range.endContainer).toBe(node);
		expect(range.endOffset).toBe(4);
		expect(range.toString()).toBe('(S1)');
	});

	it('builds one range spanning two text nodes separated by a <br> gap, for a multi-line match', () => {
		const container = document.createElement('div');
		const before = textNode('(S1) says: <d>I cry');
		const after = textNode('Underneath</d>.');
		container.appendChild(before);
		container.appendChild(document.createElement('br'));
		container.appendChild(after);

		const spans: SyntaxHighlightSpan[] = [
			{ node: before, start: 0, end: before.textContent!.length },
			{ node: after, start: before.textContent!.length + 1, end: before.textContent!.length + 1 + after.textContent!.length }
		];

		const dialogueStart = before.textContent!.indexOf('<d>');
		const dialogueEnd = spans[1].start + after.textContent!.indexOf('</d>') + '</d>'.length;

		const ranges = buildSyntaxHighlightRanges([{ start: dialogueStart, end: dialogueEnd, tone: 'signal' }], spans);

		expect(ranges.tone.get('signal')).toHaveLength(1);
		const range = ranges.tone.get('signal')![0];
		expect(range.startContainer).toBe(before);
		expect(range.endContainer).toBe(after);
		expect(range.toString()).toBe('<d>I cryUnderneath</d>');
	});

	it('skips a match whose start or end falls outside every span (an atomic chip boundary)', () => {
		const node = textNode('abc');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: 3 }];

		const ranges = buildSyntaxHighlightRanges([{ start: 0, end: 10, tone: 'info' }], spans);

		expect(ranges.tone.size).toBe(0);
		expect(ranges.color.size).toBe(0);
	});

	it('groups ranges by tone', () => {
		const node = textNode('(S1) and (S2)');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: node.textContent!.length }];

		const ranges = buildSyntaxHighlightRanges(
			[
				{ start: 0, end: 4, tone: 'accent' },
				{ start: 9, end: 13, tone: 'warning' }
			],
			spans
		);

		expect(ranges.tone.get('accent')).toHaveLength(1);
		expect(ranges.tone.get('warning')).toHaveLength(1);
		expect(ranges.color.size).toBe(0);
	});

	it('routes a match with a color into the color map, keyed by the sanitized color, not the tone map', () => {
		const node = textNode('(S1) and (S2)');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: node.textContent!.length }];

		const ranges = buildSyntaxHighlightRanges(
			[
				{ start: 0, end: 4, tone: 'accent', color: 'mediumorchid' },
				{ start: 9, end: 13, tone: 'accent', color: '#ABC' }
			],
			spans
		);

		expect(ranges.tone.size).toBe(0);
		expect(ranges.color.get('mediumorchid')).toHaveLength(1);
		expect(ranges.color.get('#abc')).toHaveLength(1);
	});

	it('drops an invalid color and falls back to the tone map', () => {
		const node = textNode('(S1)');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: 4 }];

		const ranges = buildSyntaxHighlightRanges(
			[{ start: 0, end: 4, tone: 'accent', color: 'red; } body { display: none' }],
			spans
		);

		expect(ranges.color.size).toBe(0);
		expect(ranges.tone.get('accent')).toHaveLength(1);
	});
});

describe('syntaxColorHighlightName', () => {
	it('builds a deterministic, sanitized name for a hex color', () => {
		expect(syntaxColorHighlightName('#7dd3fc')).toBe('potionui-syntax-c-7dd3fc');
	});

	it('builds a deterministic name for a named color', () => {
		expect(syntaxColorHighlightName('coral')).toBe('potionui-syntax-c-coral');
	});

	it('returns null for a value that fails sanitization', () => {
		expect(syntaxColorHighlightName('not a color!')).toBeNull();
		expect(syntaxColorHighlightName('Red')).toBeNull();
	});
});
