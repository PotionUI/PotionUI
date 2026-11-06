import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const copyText = vi.fn(async (_value: string) => true);
vi.mock('$lib/utils/clipboard', () => ({ copyText }));

const { default: TextOutputs } = await import('$lib/components/elixirs/TextOutputs.svelte');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	copyText.mockClear();
});

function render(entries: Array<Record<string, unknown>>, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(TextOutputs, { target, props: { entries: entries as never, label: 'Written prompts', stepName: 'Writer', readOnly: true, ...props } });
	flushSync();
}

const viewer = () => document.body.querySelector('[data-testid="writer-output-viewer"]');
const sections = () => [...document.body.querySelectorAll<HTMLElement>('[data-section]')].map((node) => node.dataset.section);
const toggle = (id: string) => document.body.querySelector<HTMLButtonElement>(`[data-section="${id}"] button.toggle`)!;
const openButton = (index = 0) => target.querySelectorAll<HTMLButtonElement>('button[aria-label^="Open full output"]')[index];
const key = (name: string) => {
	window.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
	flushSync();
};

const full = { kind: 'text', text_id: 't1', for_key: 'a', text: 'A fox at dusk', rolled_instruction: 'Write a noir prompt about a fox', instruction_seed: 12345, reasoning: 'One two three\n\nfour five' };
const plain = { kind: 'text', text_id: 't2', for_key: 'b', text: 'A heron', reasoning: null, rolled_instruction: null };

it('keeps the card compact: no inline toggles, only an open button', () => {
	render([full]);
	expect(target.querySelector('button.think')).toBeNull();
	expect(target.textContent).not.toContain('Write a noir prompt about a fox');
	expect(openButton()).toBeTruthy();
	expect(viewer()).toBeNull();
});

it('opens the viewer with rolled instruction, thinking and prompt in order, collapsed except the prompt', () => {
	render([full]);
	openButton().click();
	flushSync();

	expect(sections()).toEqual(['rolled', 'thinking', 'prompt']);
	expect(toggle('rolled').getAttribute('aria-expanded')).toBe('false');
	expect(toggle('thinking').getAttribute('aria-expanded')).toBe('false');
	expect(toggle('prompt').getAttribute('aria-expanded')).toBe('true');
	expect(toggle('rolled').textContent).toContain('seed 12345');
	expect(toggle('thinking').textContent).toContain('5 words');
	expect(viewer()?.textContent).toContain('A fox at dusk');
	expect(viewer()?.textContent).not.toContain('Write a noir prompt about a fox');

	toggle('rolled').click();
	flushSync();
	expect(viewer()?.textContent).toContain('Write a noir prompt about a fox');
});

it('hides sections that have no content', () => {
	render([plain]);
	openButton().click();
	flushSync();
	expect(sections()).toEqual(['prompt']);
});

it('copies a section', async () => {
	render([full]);
	openButton().click();
	flushSync();
	document.body.querySelector<HTMLButtonElement>('[data-section="prompt"] .head button:last-child')!.click();
	await Promise.resolve();
	expect(copyText).toHaveBeenCalledWith('A fox at dusk');
});

it('moves between entries with buttons and arrow keys, keeping section state', () => {
	render([full, { ...full, text_id: 't3', for_key: 'c', text: 'Second' }], { sources: { a: 'one.txt', c: 'two.txt' } });
	openButton().click();
	flushSync();
	expect(document.body.textContent).toContain('Prompt 1 of 2 · one.txt');

	toggle('thinking').click();
	flushSync();
	document.body.querySelector<HTMLButtonElement>('button[aria-label="Next prompt"]')!.click();
	flushSync();
	expect(document.body.textContent).toContain('Prompt 2 of 2 · two.txt');
	expect(viewer()?.textContent).toContain('Second');
	expect(toggle('thinking').getAttribute('aria-expanded')).toBe('true');

	key('ArrowLeft');
	expect(document.body.textContent).toContain('Prompt 1 of 2');
	key('ArrowLeft');
	expect(document.body.textContent).toContain('Prompt 1 of 2');
});

it('edits and pins through the existing callbacks', () => {
	const onedit = vi.fn();
	const onpin = vi.fn();
	render([full], { readOnly: false, onedit, onpin });
	openButton().click();
	flushSync();

	const actions = () => [...document.body.querySelectorAll<HTMLButtonElement>('[data-section="prompt"] .actions button')];
	actions().find((b) => b.textContent?.includes('Pin'))!.click();
	expect(onpin).toHaveBeenCalledWith(expect.objectContaining({ text_id: 't1' }));

	actions().find((b) => b.textContent?.includes('Edit'))!.click();
	flushSync();
	const area = document.body.querySelector<HTMLTextAreaElement>('textarea[aria-label="Edit prompt"]')!;
	area.value = 'A fox at dawn';
	area.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	actions().find((b) => b.textContent?.includes('Save'))!.click();
	expect(onedit).toHaveBeenCalledWith(expect.objectContaining({ text_id: 't1' }), 'A fox at dawn');
});

it('offers no edit or pin when read only, and closes on Escape', () => {
	render([full]);
	openButton().click();
	flushSync();
	expect(document.body.querySelector('[data-section="prompt"] .actions')).toBeNull();

	document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
	window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
	flushSync();
	expect(viewer()).toBeNull();
});
