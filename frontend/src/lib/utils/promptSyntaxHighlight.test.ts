// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { buildSyntaxHighlightRanges, type SyntaxHighlightSpan } from './promptSyntaxHighlight';

function textNode(text: string): Text {
	return document.createTextNode(text);
}

describe('buildSyntaxHighlightRanges', () => {
	it('builds one collapsed-to-node range when the match fits in a single span', () => {
		const node = textNode('(S1) says hello');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: 15 }];

		const ranges = buildSyntaxHighlightRanges([{ start: 0, end: 4, tone: 'accent' }], spans);

		expect(ranges.get('accent')).toHaveLength(1);
		const range = ranges.get('accent')![0];
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

		expect(ranges.get('signal')).toHaveLength(1);
		const range = ranges.get('signal')![0];
		expect(range.startContainer).toBe(before);
		expect(range.endContainer).toBe(after);
		expect(range.toString()).toBe('<d>I cryUnderneath</d>');
	});

	it('skips a match whose start or end falls outside every span (an atomic chip boundary)', () => {
		const node = textNode('abc');
		const spans: SyntaxHighlightSpan[] = [{ node, start: 0, end: 3 }];

		const ranges = buildSyntaxHighlightRanges([{ start: 0, end: 10, tone: 'info' }], spans);

		expect(ranges.size).toBe(0);
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

		expect(ranges.get('accent')).toHaveLength(1);
		expect(ranges.get('warning')).toHaveLength(1);
	});
});
