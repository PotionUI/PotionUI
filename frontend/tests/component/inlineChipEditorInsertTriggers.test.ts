// @vitest-environment jsdom
//
// The segment footer's "Phrasebook"/"Variable"/"Choice" Insert buttons
// (PromptSegment.svelte) call these three exported functions instead of
// requiring the user to type `#`/`$`/`{`. A caret left just after the
// inserted character's PARENT element (via `Range.setStartAfter`) rather than
// inside the text node itself failed detectPhrasebookTrigger's/
// detectVariablePickerTrigger's `startContainer.nodeType === TEXT_NODE` check
// and closed the picker right back — the trigger character stuck around with
// nothing open, exactly the maintainer's report ("it only adds the # and
// nothing happens").
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountEditor(value = '') {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, chips: {}, variant: 'segment-composer', borderless: true }
	});
	return component as unknown as {
		insertPhrasebookTrigger: () => void;
		insertVariableTrigger: () => void;
		insertChoiceGroup: () => void;
	};
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('InlineChipEditor programmatic insert triggers', () => {
	it('insertPhrasebookTrigger inserts "#" with the caret inside the text node and opens the phrasebook picker', () => {
		const editor = mountEditor();
		editor.insertPhrasebookTrigger();
		flushSync();

		const editorEl = target.querySelector('.inline-chip-editor')!;
		expect(editorEl.textContent).toBe('#');

		const range = window.getSelection()?.getRangeAt(0);
		expect(range?.startContainer.nodeType).toBe(Node.TEXT_NODE);

		expect(document.querySelector('.phrasebook-picker')).not.toBeNull();
	});

	it('insertVariableTrigger inserts "$" with the caret inside the text node and opens the variable picker', () => {
		const editor = mountEditor();
		editor.insertVariableTrigger();
		flushSync();

		const editorEl = target.querySelector('.inline-chip-editor')!;
		expect(editorEl.textContent).toBe('$');

		const range = window.getSelection()?.getRangeAt(0);
		expect(range?.startContainer.nodeType).toBe(Node.TEXT_NODE);

		expect(document.querySelector('.variable-picker')).not.toBeNull();
	});

	it('insertChoiceGroup inserts a closed {a|b} group and chip-ifies it immediately', () => {
		const editor = mountEditor();
		editor.insertChoiceGroup();
		flushSync();

		expect(target.querySelector('.choice-group-container')).not.toBeNull();
	});
});
