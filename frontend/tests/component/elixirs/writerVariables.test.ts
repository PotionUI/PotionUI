import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const { default: NodePanel } = await import('$lib/components/elixirs/NodePanel.svelte');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});

function render(variables: Record<string, unknown> | undefined) {
	const onwriterchange = vi.fn();
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodePanel, {
		target,
		props: {
			node: { id: 'writer', kind: 'llm', instruction: 'Lit by ${light}', target_node_id: null, pinned: {}, assets: [], variables } as never,
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

function variablesButton() {
	return [...target.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('Variables'))!;
}

it('the writer edits its own variables in the Generate variables manager', () => {
	const onwriterchange = render({ light: { type: 'text', value: 'candles' } });

	expect(variablesButton().textContent?.replace(/\s+/g, ' ').trim()).toBe('Variables 1');
	variablesButton().click();
	flushSync();

	const listed = document.querySelector('[role="listbox"][aria-label="Variables"]');
	expect(listed?.textContent).toContain('$light');
	expect(document.querySelector('[data-testid="variable-used-tick"]')?.getAttribute('aria-label')).toBe('Used in the prompt 1 time');

	const value = document.querySelector<HTMLInputElement>('input[aria-label="Variable value"]')!;
	value.value = 'neon';
	value.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();

	expect(onwriterchange).toHaveBeenLastCalledWith({ variables: { light: { type: 'text', value: 'neon' } } });
});

it('a writer without variables shows no count', () => {
	render(undefined);

	expect(variablesButton().textContent?.trim()).toBe('Variables');
});
