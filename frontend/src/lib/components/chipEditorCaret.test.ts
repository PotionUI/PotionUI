// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { ChipData } from '$lib/types/segments';
import { getCaretCharOffset, placeCaretAtCharOffset } from './chipEditorCaret';

function makeChip(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'cat', // encodes to "#cat" (4 chars)
		valueId: 'val-1',
		label: 'Cat',
		value: 'cat',
		allValues: [],
		shuffle: false,
		autoRegen: false,
		...overrides
	};
}

// hello <chip #cat, 4 chars> world  -- caret-relevant offsets:
// "hello " = 0..6, chip = 6..10, " world" = 10..16
function buildEditor(): { editor: HTMLDivElement; chip: HTMLElement; before: Text; after: Text } {
	const editor = document.createElement('div');
	const chipData = makeChip();
	const before = document.createTextNode('hello ');
	const chip = document.createElement('span');
	chip.dataset.chipId = chipData.id;
	chip.contentEditable = 'false';
	const after = document.createTextNode(' world');
	editor.appendChild(before);
	editor.appendChild(chip);
	editor.appendChild(after);
	document.body.appendChild(editor);
	return { editor, chip, before, after };
}

const chips: Record<string, ChipData> = { 'chip-1': makeChip() };

let cleanup: HTMLElement[] = [];
beforeEach(() => {
	cleanup = [];
});
afterEach(() => {
	cleanup.forEach((el) => el.remove());
	window.getSelection()?.removeAllRanges();
});

function withCleanup<T extends { editor: HTMLElement }>(built: T): T {
	cleanup.push(built.editor);
	return built;
}

// <chip1 #cat, 4 chars><chip2 #dog, 4 chars> -- no text nodes at all:
// chip1 = 0..4, chip2 = 4..8, end of content = 8
function buildAdjacentChipsEditor(): { editor: HTMLDivElement; chip1: HTMLElement; chip2: HTMLElement } {
	const editor = document.createElement('div');
	const chip1 = document.createElement('span');
	chip1.dataset.chipId = 'chip-1';
	chip1.contentEditable = 'false';
	const chip2 = document.createElement('span');
	chip2.dataset.chipId = 'chip-2';
	chip2.contentEditable = 'false';
	editor.appendChild(chip1);
	editor.appendChild(chip2);
	document.body.appendChild(editor);
	return { editor, chip1, chip2 };
}

const adjacentChips: Record<string, ChipData> = {
	'chip-1': makeChip({ id: 'chip-1', categoryPath: 'cat' }),
	'chip-2': makeChip({ id: 'chip-2', categoryPath: 'dog' })
};

describe('getCaretCharOffset', () => {
	it('returns null when there is no active selection range', () => {
		const { editor } = withCleanup(buildEditor());
		window.getSelection()?.removeAllRanges();
		expect(getCaretCharOffset(editor, chips)).toBeNull();
	});

	it('returns null when editorRef is null/undefined', () => {
		expect(getCaretCharOffset(null, chips)).toBeNull();
		expect(getCaretCharOffset(undefined, chips)).toBeNull();
	});

	it('returns null for a non-collapsed (range) selection', () => {
		const { editor, before, after } = withCleanup(buildEditor());
		const range = document.createRange();
		range.setStart(before, 0);
		range.setEnd(after, 2);
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		expect(getCaretCharOffset(editor, chips)).toBeNull();
	});

	it('computes the absolute offset for a caret in the text node before the chip', () => {
		const { editor, before } = withCleanup(buildEditor());
		const range = document.createRange();
		range.setStart(before, 3);
		range.collapse(true);
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		expect(getCaretCharOffset(editor, chips)).toBe(3);
	});

	it('computes the absolute offset for a caret in the text node after the chip, counting the chip as its encoded #path length', () => {
		const { editor, after } = withCleanup(buildEditor());
		const range = document.createRange();
		range.setStart(after, 2); // " w|orld"
		range.collapse(true);
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		// "hello " (6) + "#cat" (4) + 2
		expect(getCaretCharOffset(editor, chips)).toBe(12);
	});

	// When the caret sits immediately after an atomic container as produced by
	// Range.setStartAfter, startContainer === editorRef itself (per the DOM
	// spec, not a jsdom quirk) and startOffset is a child index - not a
	// same-node child of editorRef, which is all the recursive walk below
	// compares against.
	it('resolves the offset when the caret is placed via setStartAfter on the chip container (startContainer === editorRef)', () => {
		const { editor, chip } = withCleanup(buildEditor());
		const range = document.createRange();
		range.setStartAfter(chip);
		range.collapse(true);
		expect(range.startContainer).toBe(editor); // per DOM spec, not jsdom-specific
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		// "hello " (6) + "#cat" (4)
		expect(getCaretCharOffset(editor, chips)).toBe(10);
	});

	it('resolves the offset when the caret sits between two adjacent chips', () => {
		const { editor, chip1 } = withCleanup(buildAdjacentChipsEditor());
		const range = document.createRange();
		range.setStartAfter(chip1);
		range.collapse(true);
		expect(range.startContainer).toBe(editor);
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		expect(getCaretCharOffset(editor, adjacentChips)).toBe(4);
	});

	it('resolves the offset when the caret sits after the last chip with no trailing text node', () => {
		const { editor, chip2 } = withCleanup(buildAdjacentChipsEditor());
		const range = document.createRange();
		range.setStartAfter(chip2);
		range.collapse(true);
		expect(range.startContainer).toBe(editor);
		const sel = window.getSelection()!;
		sel.removeAllRanges();
		sel.addRange(range);
		expect(getCaretCharOffset(editor, adjacentChips)).toBe(8);
	});
});

describe('placeCaretAtCharOffset', () => {
	it('places the caret inside a text node at the given offset', () => {
		const { editor, before } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 3);
		const range = window.getSelection()!.getRangeAt(0);
		expect(range.startContainer).toBe(before);
		expect(range.startOffset).toBe(3);
		expect(range.collapsed).toBe(true);
	});

	it('lands just AFTER the chip container when the target offset is exactly at its trailing boundary', () => {
		const { editor, chip } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 10); // 6 ("hello ") + 4 ("#cat")
		const range = window.getSelection()!.getRangeAt(0);
		// setStartAfter semantics: anchors on the parent, offset = index+1.
		expect(range.startContainer).toBe(editor);
		expect(range.startOffset).toBe(Array.from(editor.childNodes).indexOf(chip) + 1);
	});

	it('places the caret in the text node after the chip when the offset is past the boundary', () => {
		const { editor, after } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 12); // 2 chars into " world"
		const range = window.getSelection()!.getRangeAt(0);
		expect(range.startContainer).toBe(after);
		expect(range.startOffset).toBe(2);
	});

	it('falls back to the end of editorRef content when the target offset exceeds the total length', () => {
		const { editor } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 9999);
		const range = window.getSelection()!.getRangeAt(0);
		expect(range.collapsed).toBe(true);
		// selectNodeContents(editorRef) + collapse(false) anchors on editorRef
		// at its full child count.
		expect(range.startContainer).toBe(editor);
		expect(range.startOffset).toBe(editor.childNodes.length);
	});

	it('is a no-op when editorRef is null/undefined (no throw)', () => {
		expect(() => placeCaretAtCharOffset(null, chips, 3)).not.toThrow();
		expect(() => placeCaretAtCharOffset(undefined, chips, 3)).not.toThrow();
	});

	it('round-trips with getCaretCharOffset for an offset strictly inside a text node', () => {
		const { editor } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 3);
		expect(getCaretCharOffset(editor, chips)).toBe(3);
	});

	it('round-trips with getCaretCharOffset for an offset in the text node past the chip', () => {
		const { editor } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 12);
		expect(getCaretCharOffset(editor, chips)).toBe(12);
	});

	it('round-trips with getCaretCharOffset when landing exactly at a chip boundary', () => {
		const { editor } = withCleanup(buildEditor());
		placeCaretAtCharOffset(editor, chips, 10);
		expect(getCaretCharOffset(editor, chips)).toBe(10);
	});

	it('round-trips with getCaretCharOffset when landing between two adjacent chips', () => {
		const { editor } = withCleanup(buildAdjacentChipsEditor());
		placeCaretAtCharOffset(editor, adjacentChips, 4);
		expect(getCaretCharOffset(editor, adjacentChips)).toBe(4);
	});

	it('round-trips with getCaretCharOffset when landing after the last chip with no trailing text node', () => {
		const { editor } = withCleanup(buildAdjacentChipsEditor());
		placeCaretAtCharOffset(editor, adjacentChips, 8);
		expect(getCaretCharOffset(editor, adjacentChips)).toBe(8);
	});
});
