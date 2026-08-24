// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import type { ChipData } from '$lib/types/segments';
import type { ContentSegment } from './chipSegments';
import {
	buildSegmentNode,
	extractContentFromDOM,
	collectTextNodeSpans,
	atomicContainerAt,
	atomicDeletionTarget
} from './chipEditorDom';

function makeChip(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'emotions.happy',
		valueId: 'val-1',
		label: 'Happy',
		value: 'happy',
		allValues: [],
		shuffle: false,
		autoRegen: false,
		...overrides
	};
}

describe('buildSegmentNode', () => {
	it('builds a plain text node for a text segment', () => {
		const node = buildSegmentNode({ type: 'text', content: 'hello' });
		expect(node.nodeType).toBe(Node.TEXT_NODE);
		expect(node.textContent).toBe('hello');
	});

	it('builds a contenteditable=false chip container carrying data-chip-id', () => {
		const chip = makeChip();
		const node = buildSegmentNode({ type: 'chip', content: '#emotions.happy', chipId: chip.id, chipData: chip });
		const el = node as HTMLElement;
		expect(el.tagName).toBe('SPAN');
		expect(el.dataset.chipId).toBe('chip-1');
		expect(el.contentEditable).toBe('false');
		expect(el.className).toBe('inline-chip-container');
	});

	it('builds a group container carrying data-group-raw', () => {
		const node = buildSegmentNode({ type: 'group', content: '{a|b}', groupRaw: '{a|b}' });
		const el = node as HTMLElement;
		expect(el.dataset.groupRaw).toBe('{a|b}');
		expect(el.className).toBe('choice-group-container');
	});

	it('builds a variable container carrying data-variable-raw and data-variable-name', () => {
		const node = buildSegmentNode({
			type: 'variable',
			content: '${mood}',
			variableRaw: '${mood}',
			variableName: 'mood'
		});
		const el = node as HTMLElement;
		expect(el.dataset.variableRaw).toBe('${mood}');
		expect(el.dataset.variableName).toBe('mood');
		expect(el.className).toBe('variable-usage-container');
	});

	it('falls back to a text node for a chip segment missing chipId/chipData', () => {
		const node = buildSegmentNode({ type: 'chip', content: '#broken' });
		expect(node.nodeType).toBe(Node.TEXT_NODE);
		expect(node.textContent).toBe('#broken');
	});

	it('falls back to a text node for a group segment missing groupRaw', () => {
		const node = buildSegmentNode({ type: 'group', content: '{a|b}' });
		expect(node.nodeType).toBe(Node.TEXT_NODE);
	});
});

function appendSegments(editor: HTMLElement, segments: ContentSegment[]) {
	for (const seg of segments) editor.appendChild(buildSegmentNode(seg));
}

describe('extractContentFromDOM', () => {
	it('returns empty value/chips when editorRef is null/undefined', () => {
		expect(extractContentFromDOM(null, {})).toEqual({ value: '', chips: {} });
		expect(extractContentFromDOM(undefined, {})).toEqual({ value: '', chips: {} });
	});

	it('concatenates plain text nodes', () => {
		const editor = document.createElement('div');
		editor.appendChild(document.createTextNode('hello world'));
		expect(extractContentFromDOM(editor, {})).toEqual({ value: 'hello world', chips: {} });
	});

	it('re-encodes a chip container back to its #path marker and returns the chip in the extracted map', () => {
		const chip = makeChip();
		const editor = document.createElement('div');
		appendSegments(editor, [
			{ type: 'text', content: 'a ' },
			{ type: 'chip', content: '#emotions.happy', chipId: chip.id, chipData: chip },
			{ type: 'text', content: ' face' }
		]);
		const result = extractContentFromDOM(editor, { [chip.id]: chip });
		expect(result.value).toBe('a #emotions.happy face');
		expect(result.chips).toEqual({ [chip.id]: chip });
	});

	it('drops a chip container whose id is missing from the chips map (orphaned container)', () => {
		const editor = document.createElement('div');
		appendSegments(editor, [{ type: 'chip', content: '#x', chipId: 'ghost', chipData: makeChip() }]);
		const result = extractContentFromDOM(editor, {});
		expect(result).toEqual({ value: '', chips: {} });
	});

	it('reads a group container back verbatim from its data-group-raw attribute, not chip lookup', () => {
		const editor = document.createElement('div');
		appendSegments(editor, [{ type: 'group', content: '{a|b}', groupRaw: '{a|b}' }]);
		expect(extractContentFromDOM(editor, {}).value).toBe('{a|b}');
	});

	it('reads a variable container back verbatim from its data-variable-raw attribute', () => {
		const editor = document.createElement('div');
		appendSegments(editor, [{ type: 'variable', content: '${mood}', variableRaw: '${mood}', variableName: 'mood' }]);
		expect(extractContentFromDOM(editor, {}).value).toBe('${mood}');
	});

	it('renders a BR element as a newline', () => {
		const editor = document.createElement('div');
		editor.appendChild(document.createTextNode('line1'));
		editor.appendChild(document.createElement('br'));
		editor.appendChild(document.createTextNode('line2'));
		expect(extractContentFromDOM(editor, {}).value).toBe('line1\nline2');
	});

	it('appends a newline after a DIV block element that has a following sibling', () => {
		const editor = document.createElement('div');
		const block = document.createElement('div');
		block.appendChild(document.createTextNode('para'));
		editor.appendChild(block);
		editor.appendChild(document.createTextNode('next'));
		expect(extractContentFromDOM(editor, {}).value).toBe('para\nnext');
	});

	it('does not append a trailing newline after a DIV block that is the last child', () => {
		const editor = document.createElement('div');
		const block = document.createElement('div');
		block.appendChild(document.createTextNode('para'));
		editor.appendChild(block);
		expect(extractContentFromDOM(editor, {}).value).toBe('para');
	});
});

describe('collectTextNodeSpans', () => {
	it('returns one span per text node with correct absolute offsets, skipping atomic containers', () => {
		const chip = makeChip();
		const editor = document.createElement('div');
		appendSegments(editor, [
			{ type: 'text', content: 'hello ' }, // 0-6
			{ type: 'chip', content: '#emotions.happy', chipId: chip.id, chipData: chip }, // contributes len("#emotions.happy")=15 -> 6-21
			{ type: 'text', content: ' world' } // 21-27
		]);
		const spans = collectTextNodeSpans(editor, { [chip.id]: chip });
		expect(spans).toHaveLength(2);
		expect(spans[0]).toMatchObject({ start: 0, end: 6 });
		expect(spans[0].node.textContent).toBe('hello ');
		expect(spans[1]).toMatchObject({ start: 21, end: 27 });
		expect(spans[1].node.textContent).toBe(' world');
	});

	it('a chip container whose id is missing from chips contributes zero width (not skipped, not counted)', () => {
		const editor = document.createElement('div');
		appendSegments(editor, [
			{ type: 'text', content: 'a' },
			{ type: 'chip', content: '#x', chipId: 'ghost', chipData: makeChip() },
			{ type: 'text', content: 'b' }
		]);
		const spans = collectTextNodeSpans(editor, {});
		expect(spans[0]).toMatchObject({ start: 0, end: 1 });
		// Unresolved chip container contributes 0 to offset, so the second text
		// node's span starts immediately after the first, not after any gap.
		expect(spans[1]).toMatchObject({ start: 1, end: 2 });
	});

	it('counts a BR as a single-character offset', () => {
		const editor = document.createElement('div');
		editor.appendChild(document.createTextNode('a'));
		editor.appendChild(document.createElement('br'));
		editor.appendChild(document.createTextNode('b'));
		const spans = collectTextNodeSpans(editor, {});
		expect(spans[0]).toMatchObject({ start: 0, end: 1 });
		expect(spans[1]).toMatchObject({ start: 2, end: 3 });
	});
});

describe('atomicContainerAt', () => {
	it('classifies each atomic container kind', () => {
		const chip = makeChip();
		expect(
			atomicContainerAt(
				buildSegmentNode({ type: 'chip', content: '#c', chipId: chip.id, chipData: chip })
			)
		).toMatchObject({ kind: 'chip', chipId: 'chip-1' });
		expect(
			atomicContainerAt(buildSegmentNode({ type: 'group', content: '{a|b}', groupRaw: '{a|b}' }))
		).toMatchObject({ kind: 'group' });
		expect(
			atomicContainerAt(
				buildSegmentNode({ type: 'variable', content: '${m}', variableRaw: '${m}', variableName: 'm' })
			)
		).toMatchObject({ kind: 'variable' });
	});

	it('is null for text nodes, plain elements and nothing', () => {
		expect(atomicContainerAt(document.createTextNode('x'))).toBeNull();
		expect(atomicContainerAt(document.createElement('span'))).toBeNull();
		expect(atomicContainerAt(null)).toBeNull();
	});
});

describe('atomicDeletionTarget', () => {
	// "hello " <chip> " world"
	function buildEditor() {
		const chip = makeChip();
		const editor = document.createElement('div');
		const before = document.createTextNode('hello ');
		const chipEl = buildSegmentNode({
			type: 'chip',
			content: '#emotions.happy',
			chipId: chip.id,
			chipData: chip
		}) as HTMLElement;
		const after = document.createTextNode(' world');
		editor.append(before, chipEl, after);
		return { editor, before, chipEl, after };
	}

	describe('backward (Backspace)', () => {
		it('targets the chip when the caret is at the start of the text after it', () => {
			const { editor, after } = buildEditor();
			expect(atomicDeletionTarget(editor, after, 0, 'backward')).toMatchObject({
				kind: 'chip',
				chipId: 'chip-1'
			});
		});

		it('targets the chip from a caret anchored on the editor just after it', () => {
			const { editor } = buildEditor();
			// setStartAfter(chip) anchors on the editor with a CHILD INDEX of 2.
			expect(atomicDeletionTarget(editor, editor, 2, 'backward')).toMatchObject({ kind: 'chip' });
		});

		it('is null mid-text (an ordinary character delete)', () => {
			const { editor, after } = buildEditor();
			expect(atomicDeletionTarget(editor, after, 3, 'backward')).toBeNull();
		});
	});

	// The bug: Delete in front of a chip did nothing at all, because only
	// Backspace was ever intercepted and the browser will not remove a
	// contentEditable=false span by itself.
	describe('forward (Delete)', () => {
		it('targets the chip when the caret is at the end of the text before it', () => {
			const { editor, before } = buildEditor();
			expect(atomicDeletionTarget(editor, before, 'hello '.length, 'forward')).toMatchObject({
				kind: 'chip',
				chipId: 'chip-1'
			});
		});

		it('targets the chip from a caret anchored on the editor just before it', () => {
			const { editor } = buildEditor();
			expect(atomicDeletionTarget(editor, editor, 1, 'forward')).toMatchObject({ kind: 'chip' });
		});

		it('is null mid-text and at the start of the text before a chip', () => {
			const { editor, before } = buildEditor();
			expect(atomicDeletionTarget(editor, before, 0, 'forward')).toBeNull();
			expect(atomicDeletionTarget(editor, before, 2, 'forward')).toBeNull();
		});

		it('is null at the very end of the content', () => {
			const { editor, after } = buildEditor();
			expect(atomicDeletionTarget(editor, after, ' world'.length, 'forward')).toBeNull();
			expect(atomicDeletionTarget(editor, editor, editor.childNodes.length, 'forward')).toBeNull();
		});

		it('removes a group and a variable the same way it removes a chip', () => {
			const editor = document.createElement('div');
			const text = document.createTextNode('a');
			const group = buildSegmentNode({ type: 'group', content: '{a|b}', groupRaw: '{a|b}' });
			const variable = buildSegmentNode({
				type: 'variable',
				content: '${m}',
				variableRaw: '${m}',
				variableName: 'm'
			});
			editor.append(text, group, variable);
			expect(atomicDeletionTarget(editor, text, 1, 'forward')).toMatchObject({ kind: 'group' });
			expect(atomicDeletionTarget(editor, editor, 2, 'forward')).toMatchObject({ kind: 'variable' });
		});

		it('does not fire when a plain text node follows the caret', () => {
			const editor = document.createElement('div');
			const a = document.createTextNode('ab');
			const b = document.createTextNode('cd');
			editor.append(a, b);
			expect(atomicDeletionTarget(editor, a, 2, 'forward')).toBeNull();
		});
	});

	it('is null without an editor or a container', () => {
		const { editor, before } = buildEditor();
		expect(atomicDeletionTarget(null, before, 0, 'forward')).toBeNull();
		expect(atomicDeletionTarget(editor, null, 0, 'forward')).toBeNull();
	});
});
