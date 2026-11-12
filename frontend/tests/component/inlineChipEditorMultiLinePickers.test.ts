import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
import type { VariablesMap } from '$lib/utils/variableDefs';
import { api } from '$lib/services/api/index';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;
let values: string[];

const variables: VariablesMap = {
	hair: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['red', 'blue'] }
};

const syntax: PromptSyntaxSpec[] = [{ token: 'BREAK', kind: 'marker', help: 'Splits into CLIP chunks' }];

const resourceSpecs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }
];

const twoResourceGroups: PromptResourceSpec[] = [
	...resourceSpecs,
	{ field: 'reference_videos', kind: 'video', token: '<Video @>' }
];

const pictures = [{ relative_path: 'a.png', name: 'a.png', url: '/a.png' }];
const videos = [{ relative_path: 'v.mp4', name: 'v.mp4', url: '/v.mp4' }];

const twoVariables: VariablesMap = {
	...variables,
	mood: { type: 'choice', mode: 'shuffle', pinnedIndex: null, options: ['calm', 'tense'] }
};

const twoSyntax: PromptSyntaxSpec[] = [...syntax, { token: 'AND', kind: 'marker', help: 'Joins two prompts' }];

const searchHit = (id: string, label: string) => ({
	id,
	category_path: label,
	label,
	value: `value of ${label}`
});

interface LineShape {
	name: string;
	html: string;
	prefix: string;
	suffix: string;
}

const SHAPES: LineShape[] = [
	{
		name: 'a first line of text and two block lines',
		html: 'line one<div>line two</div><div>{}</div>',
		prefix: 'line one\nline two\n',
		suffix: ''
	},
	{
		name: 'a blank block line between lines',
		html: 'line one<div><br></div><div>{}</div>',
		prefix: 'line one\n\n',
		suffix: ''
	},
	{
		name: 'block lines only',
		html: '<div>line one</div><div>{}</div>',
		prefix: 'line one\n',
		suffix: ''
	},
	{
		name: 'text after the trigger on a later line',
		html: 'line one<div>line two {}tail</div>',
		prefix: 'line one\nline two ',
		suffix: 'tail'
	},
	{
		name: 'a trigger on the first line before a block',
		html: 'start {}tail<div>line two</div>',
		prefix: 'start ',
		suffix: 'tail\nline two'
	}
];

function mountEditor(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	values = [];
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	component.$on?.('change', (event: CustomEvent) => {
		values.push(event.detail.value);
	});
	return target.querySelector<HTMLElement>('.inline-chip-editor')!;
}

function typeInto(editor: HTMLElement, html: string, typed: string) {
	editor.innerHTML = html.replace('{}', typed);
	const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
	let node = walker.nextNode() as Text | null;
	while (node && !node.data.includes(typed)) node = walker.nextNode() as Text | null;
	const range = document.createRange();
	range.setStart(node!, node!.data.indexOf(typed) + typed.length);
	range.collapse(true);
	const selection = window.getSelection()!;
	selection.removeAllRanges();
	selection.addRange(range);
	editor.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function clickRow(text: string) {
	const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row'));
	rows.find((row) => row.textContent?.includes(text))?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

function press(editor: HTMLElement, key: string) {
	editor.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
	flushSync();
}

const activeRow = () => document.querySelector('.picker-row[aria-selected="true"]')?.textContent ?? '';

function mockSearch(byPath: Record<string, unknown[]>) {
	vi.spyOn(api, 'searchPhrasebook').mockImplementation((async (path: string) => ({
		success: true,
		data: { child_categories: [], values: byPath[path] ?? [] }
	})) as never);
}

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
	vi.useRealTimers();
});

describe.each(SHAPES)('InlineChipEditor pickers over $name', ({ html, prefix, suffix }) => {
	it('the $ variable picker splices the variable in at the typed $', () => {
		const editor = mountEditor({ variables });
		typeInto(editor, html, '$ha');
		expect(document.querySelector('.variable-picker')).not.toBeNull();
		clickRow('hair');
		expect(values.at(-1)).toBe(`${prefix}\${hair}${suffix}`);
	});

	it('the / syntax picker splices the token in at the typed /', () => {
		const editor = mountEditor({ promptSyntax: syntax });
		typeInto(editor, html, '/BR');
		expect(document.querySelector('.syntax-picker')).not.toBeNull();
		clickRow('BREAK');
		expect(values.at(-1)).toBe(`${prefix}BREAK${suffix}`);
	});

	it('the @ resource picker splices the marker in at the typed @', () => {
		const editor = mountEditor({
			promptResources: resourceSpecs,
			resourceFieldValues: { references: [{ relative_path: 'a.png', name: 'a.png', url: '/a.png' }] }
		});
		typeInto(editor, html, '@');
		expect(document.querySelector('.resource-picker')).not.toBeNull();
		clickRow('Pictures');
		clickRow('a.png');
		expect(values.at(-1)).toBe(`${prefix}@[references:a.png]${suffix}`);
	});

	it('the # phrasebook picker splices the chip in at the typed #', async () => {
		vi.spyOn(api, 'searchPhrasebook').mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{
						id: 'v1',
						category_path: 'Look description',
						label: 'Look description',
						value: 'A slender woman in her early 20s wearing a black blazer.'
					}
				]
			}
		} as never);
		const editor = mountEditor();
		typeInto(editor, html, '#Look description');
		expect(document.querySelector('.phrasebook-picker')).not.toBeNull();
		await vi.advanceTimersByTimeAsync(250);
		flushSync();
		clickRow('Look description');
		expect(values.at(-1)).toBe(`${prefix}#[Look description]${suffix}`);
	});
});

describe('InlineChipEditor phrasebook picker with nothing to pick', () => {
	it('Enter on the empty picker throws nothing and changes nothing', async () => {
		const errors: unknown[] = [];
		const onError = (event: ErrorEvent) => {
			errors.push(event.error);
			event.preventDefault();
		};
		window.addEventListener('error', onError);
		mockSearch({});
		const editor = mountEditor();
		typeInto(editor, '{}', '#zzz');
		await vi.advanceTimersByTimeAsync(250);
		flushSync();
		const before = values.length;
		press(editor, 'Enter');
		window.removeEventListener('error', onError);

		expect(errors).toEqual([]);
		expect(values).toHaveLength(before);
		expect(editor.textContent).toBe('#zzz');
		expect(document.querySelector('.phrasebook-picker')).not.toBeNull();
	});
});

describe('InlineChipEditor picker arrow keys on an empty list', () => {
	function arrowOnEmpty(editor: HTMLElement) {
		press(editor, 'ArrowDown');
		press(editor, 'ArrowUp');
		press(editor, 'ArrowDown');
	}

	it('the @ picker selects its first item once an emptied group fills again, and Enter picks it', () => {
		const editor = mountEditor({ promptResources: resourceSpecs, resourceFieldValues: { references: pictures } });
		typeInto(editor, '{}', '@');
		clickRow('Pictures');
		component!.$set({ resourceFieldValues: { references: [] } });
		flushSync();
		arrowOnEmpty(editor);
		component!.$set({ resourceFieldValues: { references: pictures } });
		flushSync();
		expect(activeRow()).toContain('a.png');
		press(editor, 'Enter');
		expect(values.at(-1)).toBe('@[references:a.png]');
	});

	it('the $ picker selects its first variable once variables arrive, and Enter picks it', () => {
		const editor = mountEditor({ variables: {} });
		typeInto(editor, '{}', '$');
		arrowOnEmpty(editor);
		component!.$set({ variables });
		flushSync();
		expect(activeRow()).toContain('hair');
		press(editor, 'Enter');
		expect(values.at(-1)).toBe('${hair}');
	});

	it('the / picker selects its first token once tokens arrive, and Enter picks it', () => {
		const editor = mountEditor({ promptSyntax: [] });
		typeInto(editor, '{}', '/');
		arrowOnEmpty(editor);
		component!.$set({ promptSyntax: syntax });
		flushSync();
		expect(activeRow()).toContain('BREAK');
		press(editor, 'Enter');
		expect(values.at(-1)).toBe('BREAK');
	});
});

describe('InlineChipEditor picker arrow keys wrap around a non-empty list', () => {
	it('the @ picker moves between groups and wraps at both ends', () => {
		const editor = mountEditor({
			promptResources: twoResourceGroups,
			resourceFieldValues: { references: pictures, reference_videos: videos }
		});
		typeInto(editor, '{}', '@');
		expect(activeRow()).toContain('Pictures');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('Videos');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('Kits');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('Pictures');
		press(editor, 'ArrowUp');
		expect(activeRow()).toContain('Kits');
	});

	it('the # picker moves between values and wraps at both ends, and Enter picks the active one', async () => {
		mockSearch({ Look: [searchHit('v1', 'Look first'), searchHit('v2', 'Look second')] });
		const editor = mountEditor();
		typeInto(editor, '{}', '#Look');
		await vi.advanceTimersByTimeAsync(250);
		flushSync();
		expect(activeRow()).toContain('Look first');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('Look second');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('Look first');
		press(editor, 'ArrowUp');
		expect(activeRow()).toContain('Look second');
		press(editor, 'Enter');
		expect(values.at(-1)).toBe('#[Look second]');
	});

	it('the $ picker moves between variables and wraps at both ends', () => {
		const editor = mountEditor({ variables: twoVariables });
		typeInto(editor, '{}', '$');
		expect(activeRow()).toContain('hair');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('mood');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('hair');
		press(editor, 'ArrowUp');
		expect(activeRow()).toContain('mood');
	});

	it('the / picker moves between tokens and wraps at both ends', () => {
		const editor = mountEditor({ promptSyntax: twoSyntax });
		typeInto(editor, '{}', '/');
		expect(activeRow()).toContain('BREAK');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('AND');
		press(editor, 'ArrowDown');
		expect(activeRow()).toContain('BREAK');
		press(editor, 'ArrowUp');
		expect(activeRow()).toContain('AND');
	});
});
