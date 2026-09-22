// @vitest-environment jsdom
//
// Setting a condition through VariableConditionChip's popover (inside
// VariableManagerModal) updates the owning option's `when` and the header
// badge that names the resolve order — end to end through the real DOM, not
// a mock of the popover.
import { describe, it, expect, afterEach } from 'vitest';
import { tick } from 'svelte';
import type { VariablesMap } from '$lib/utils/variableDefs';

const { default: VariableManagerModal } = await import('../../src/lib/components/VariableManagerModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountModal(variables: VariablesMap) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: VariableManagerModal as never,
		target,
		props: { isOpen: true, variables }
	});
	return { target, component, destroy: () => component.$destroy() };
}

let mounted: ReturnType<typeof mountModal> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	document.body.innerHTML = '';
});

function inputWithValue(value: string): HTMLInputElement {
	const input = Array.from(document.querySelectorAll<HTMLInputElement>('input')).find((i) => i.value === value);
	if (!input) throw new Error(`no input with value "${value}"`);
	return input;
}

function buttonByLabel(label: string): HTMLButtonElement {
	const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
		(b) => b.getAttribute('aria-label') === label
	);
	if (!button) throw new Error(`no button with aria-label "${label}"`);
	return button;
}

describe('VariableManagerModal condition editing', () => {
	it('setting a condition via the popover updates the option and shows the after/used-by badges', async () => {
		const variables: VariablesMap = {
			music: { type: 'choice', options: ['hip hop', 'classical'], mode: 'shuffle', pinnedIndex: null },
			dance: { type: 'choice', options: ['breaking', 'waltz'], mode: 'shuffle', pinnedIndex: null }
		};
		mounted = mountModal(variables);

		// BaseModal portals its content straight onto <body>, not into `target`.
		expect(document.body.textContent).not.toContain('after');
		expect(document.body.textContent).not.toContain('used by');

		let latest: VariablesMap | null = null;
		mounted.component.$on('change', (e: CustomEvent<VariablesMap>) => {
			latest = e.detail;
		});

		buttonByLabel('Set condition for this option of dance').click();
		await tick();
		buttonByLabel('Condition on $music').click();
		await tick();
		buttonByLabel('Toggle hip hop').click();
		await tick();

		expect(latest).not.toBeNull();
		const danceDef = (latest as unknown as VariablesMap).dance as any;
		expect(danceDef.options[0]).toEqual({ text: 'breaking', when: { var: 'music', values: ['hip hop'] } });
		expect(danceDef.options[1]).toBe('waltz');

		expect(document.body.textContent).toContain('after');
		expect(document.body.textContent).toContain('$music');
		expect(document.body.textContent).toContain('used by');
		expect(document.body.textContent).toContain('$dance');

		expect(inputWithValue('breaking')).toBeTruthy();
	});
});
