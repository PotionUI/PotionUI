import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const { default: TextOutputs } = await import('$lib/components/elixirs/TextOutputs.svelte');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
});

function render(entries: Array<Record<string, unknown>>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(TextOutputs, { target, props: { entries: entries as never, label: 'Written prompts', readOnly: true } });
	flushSync();
}

function toggles() {
	return [...target.querySelectorAll<HTMLButtonElement>('button.think')].map((button) => button.textContent?.trim());
}

it('shows the rolled instruction collapsed, above the thinking, and opens it on click', () => {
	render([
		{ kind: 'text', text_id: 't1', for_key: '-', text: 'A fox at dusk', rolled_instruction: 'Write a noir prompt about a fox', instruction_seed: 7, reasoning: 'Thought about it' }
	]);

	expect(toggles()).toEqual(['Rolled instruction', 'Thinking']);
	expect(target.textContent).not.toContain('Write a noir prompt about a fox');

	target.querySelector<HTMLButtonElement>('button.think')!.click();
	flushSync();

	const shown = target.querySelector('[aria-label="Rolled instruction for Prompt"]');
	expect(shown?.textContent).toBe('Write a noir prompt about a fox');
	expect(target.querySelector('button.think')?.getAttribute('aria-expanded')).toBe('true');
});

it('shows no rolled instruction for a plain instruction', () => {
	render([{ kind: 'text', text_id: 't1', for_key: '-', text: 'A fox at dusk' }]);

	expect(toggles()).toEqual([]);
});
