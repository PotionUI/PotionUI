import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { writable } from 'svelte/store';
import NodePanelLiveFixture from '../fixtures/NodePanelLiveFixture.svelte';

let component: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});

function writer(instruction: string, title = 'Describe') {
	return { id: 'writer', kind: 'llm', title, instruction, instruction_chips: {}, target_node_id: null, pinned: {}, assets: [] };
}

function render() {
	const node = writable(writer('Old instruction'));
	const onwriterchange = vi.fn();
	const onrename = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodePanelLiveFixture, { target, props: { node, onwriterchange, onrename } });
	flushSync();
	return { node, onwriterchange, onrename, target };
}

function instructionText(target: HTMLElement) {
	return target.querySelector('[data-testid="elixir-node-instruction"]')?.textContent ?? '';
}

it('an open writer panel shows an instruction changed elsewhere', async () => {
	const { node, target } = render();
	expect(instructionText(target)).toContain('Old instruction');

	node.set(writer('New instruction from PotionAI'));
	flushSync();

	await vi.waitFor(() => expect(instructionText(target)).toContain('New instruction from PotionAI'));
	expect(instructionText(target)).not.toContain('Old instruction');
});

it('leaving an untouched instruction box does not write the old instruction back', () => {
	const { node, onwriterchange, target } = render();

	node.set(writer('New instruction from PotionAI'));
	flushSync();
	target.querySelector('[data-testid="elixir-node-instruction"]')!.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
	flushSync();

	expect(onwriterchange).not.toHaveBeenCalled();
});

it('an untouched name follows a rename made elsewhere and is not written back on blur', () => {
	const { node, onrename, target } = render();
	const input = target.querySelector<HTMLInputElement>('#elixir-node-title')!;

	node.set(writer('Old instruction', 'Renamed by PotionAI'));
	flushSync();
	expect(input.value).toBe('Renamed by PotionAI');

	input.dispatchEvent(new FocusEvent('blur'));
	flushSync();
	expect(onrename).not.toHaveBeenCalled();
});
