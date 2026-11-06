import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/utils/clipboard', () => ({ copyText: vi.fn(async () => true) }));

const { default: TextOutputs } = await import('$lib/components/elixirs/TextOutputs.svelte');
const { default: NodePanel } = await import('$lib/components/elixirs/NodePanel.svelte');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
});

function render(entries: Array<Record<string, unknown>>, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(TextOutputs, { target, props: { entries: entries as never, label: 'Written prompts', stepName: 'Writer', ...props } });
	flushSync();
}

const written = (number: number, forKey = 'library:p1') => ({
	kind: 'text',
	text_id: `t${forKey}${number}`,
	for_key: forKey,
	slot_key: number === 1 ? forKey : `${forKey}#${number}`,
	prompt_number: number,
	prompt_count: 2,
	text: `prompt ${number} for ${forKey}`
});

it('labels each prompt with its place among the prompts written for its input', () => {
	render([written(1), written(2), written(1, 'library:p2')], { sources: { 'library:p1': 'p1.png', 'library:p2': 'p2.png' } });

	expect([...target.querySelectorAll('[data-testid="prompt-position"]')].map((node) => node.textContent)).toEqual(['1/2', '2/2', '1/2']);
	expect(target.querySelector('button[aria-label="Edit p1.png · prompt 2 of 2"]')).toBeTruthy();
});

it('opens the viewer on prompt k of n for that input', () => {
	render([written(1), written(2)], { sources: { 'library:p1': 'p1.png' } });

	target.querySelectorAll<HTMLButtonElement>('button[aria-label^="Open full output"]')[1].click();
	flushSync();

	expect(document.body.textContent).toContain('Prompt 2 of 2 · p1.png');
});

it('pins a prompt by its own slot so the other prompts of the input stay unpinned', () => {
	const onpin = vi.fn();
	render([written(1), written(2)], { pinned: { 'library:p1#2': 'kept' }, onpin });

	const pins = [...target.querySelectorAll<HTMLButtonElement>('button[aria-label^="Pin "], button[aria-label^="Unpin "]')];
	expect(pins.map((button) => button.getAttribute('aria-label'))).toEqual(['Pin Prompt 1 of 2', 'Unpin Prompt 2 of 2']);
	pins[0].click();
	expect(onpin.mock.calls[0][0].slot_key).toBe('library:p1');
});

function renderPanel(promptsPerInput?: number) {
	const onwriterchange = vi.fn();
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodePanel, {
		target,
		props: {
			node: { id: 'writer', kind: 'llm', instruction: 'Describe it', target_node_id: null, pinned: {}, assets: [], prompts_per_input: promptsPerInput } as never,
			client: {} as never,
			label: 'Describe',
			presets: [],
			onrename: () => {},
			onsettingschange: () => {},
			onchoosepreset: () => {},
			onpickmedia: () => {},
			onremoveasset: () => {},
			writer: { models: [], targets: [], targetLabel: null, problem: null },
			onwriterchange
		}
	});
	flushSync();
	return onwriterchange;
}

it('the writer settings set how many prompts it writes per input, from 1 to 20', () => {
	const onwriterchange = renderPanel();
	const input = target.querySelector<HTMLInputElement>('input[aria-label="Prompts per input"]')!;

	expect(input.value).toBe('1');
	expect(target.querySelector<HTMLButtonElement>('button[aria-label="Decrease Prompts per input"]')?.disabled).toBe(true);
	target.querySelector<HTMLButtonElement>('button[aria-label="Increase Prompts per input"]')!.click();
	expect(onwriterchange).toHaveBeenLastCalledWith({ prompts_per_input: 2 });

	input.value = '50';
	input.dispatchEvent(new Event('input', { bubbles: true }));
	input.dispatchEvent(new Event('blur'));
	expect(onwriterchange).toHaveBeenLastCalledWith({ prompts_per_input: 20 });
});
