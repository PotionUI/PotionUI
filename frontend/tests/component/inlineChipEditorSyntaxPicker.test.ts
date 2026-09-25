// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync, tick } from 'svelte';
import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptSyntaxSpec[] = [
	{ token: 'BREAK', kind: 'marker', help: 'Splits into CLIP chunks' },
	{
		token: '<d></d>',
		kind: 'wrap',
		pattern: '<d>[\\s\\S]*?</d>',
		insert: '<d>{}</d>',
		help: "Wraps a speaker's exact words"
	},
	{
		token: '<Subject 1>',
		kind: 'marker',
		pattern: '<Subject \\d+>',
		insert: '<Subject {n=1}>',
		help: 'References a Subject'
	}
];

interface EditorInstance {
	insertSyntaxTrigger: () => void;
}

function mountEditor(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, promptSyntax: specs, ...props }
	});
	return component as unknown as EditorInstance;
}

function clickRow(text: string) {
	const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row'));
	const row = rows.find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('InlineChipEditor / prompt-syntax picker', () => {
	it('insertSyntaxTrigger inserts "/" with the caret inside the text node and opens the syntax picker', () => {
		const editor = mountEditor();
		editor.insertSyntaxTrigger();
		flushSync();

		expect(target.querySelector('.inline-chip-editor')!.textContent).toBe('/');
		const range = window.getSelection()?.getRangeAt(0);
		expect(range?.startContainer.nodeType).toBe(Node.TEXT_NODE);
		expect(document.querySelector('.syntax-picker')).not.toBeNull();
	});

	it('lists the mode\'s tokens with their help text and filters by typing', () => {
		const editor = mountEditor();
		editor.insertSyntaxTrigger();
		flushSync();

		const text = document.querySelector('.syntax-picker')!.textContent || '';
		expect(text).toContain('BREAK');
		expect(text).toContain('<d></d>');
		expect(text).toContain('<Subject 1>');
	});

	it('selecting a plain marker inserts it and closes the picker', () => {
		const onChange = vi.fn();
		mountEditor();
		component!.$on?.('change', onChange);

		(component as unknown as EditorInstance).insertSyntaxTrigger();
		flushSync();
		clickRow('BREAK');

		expect(document.querySelector('.syntax-picker')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('BREAK');
	});

	it('selecting a wrap kind with no active selection places the caret between the tags', () => {
		mountEditor();
		(component as unknown as EditorInstance).insertSyntaxTrigger();
		flushSync();
		clickRow('<d></d>');

		expect(target.querySelector('.inline-chip-editor')!.textContent).toBe('<d></d>');
	});

	it('selecting an entry with an editable {name=default} param inserts the default and selects it', async () => {
		mountEditor();
		component!.$on?.('change', (e: CustomEvent) => {
			component!.$set?.({ value: e.detail.value, chips: e.detail.chips });
		});

		(component as unknown as EditorInstance).insertSyntaxTrigger();
		flushSync();
		clickRow('<Subject 1>');
		await tick();
		await tick();

		expect(target.querySelector('.inline-chip-editor')!.textContent).toBe('<Subject 1>');
		const selection = window.getSelection();
		expect(selection?.toString()).toBe('1');
	});
});
