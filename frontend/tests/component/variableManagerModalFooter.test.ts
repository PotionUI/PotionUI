// @vitest-environment jsdom
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

function buttonWithText(text: string): HTMLButtonElement {
	const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
		b.textContent?.trim().startsWith(text)
	);
	if (!button) throw new Error(`no button with text "${text}"`);
	return button;
}

function footerContainer(): HTMLElement {
	const el = document.querySelector<HTMLElement>('.border-t.border-line');
	if (!el) throw new Error('footer container not found');
	return el;
}

describe('VariableManagerModal footer', () => {
	it('renders Copy and Paste inside the footer, not the scrollable body', async () => {
		mounted = mountModal({ mood: { type: 'text', value: 'moody' } });
		await tick();

		const footer = footerContainer();
		expect(footer.contains(buttonWithText('Copy'))).toBe(true);
		expect(footer.contains(buttonWithText('Paste'))).toBe(true);
		expect(footer.contains(buttonWithText('Add variable'))).toBe(false);
	});

	it('confirms via the Done button and settles a single close event', async () => {
		mounted = mountModal({ mood: { type: 'text', value: 'moody' } });
		await tick();

		let closeCount = 0;
		mounted.component.$on('close', () => {
			closeCount += 1;
		});

		buttonWithText('Done').click();
		await tick();

		expect(closeCount).toBe(1);
	});

	it('cancels via the Cancel button and settles a single close event', async () => {
		mounted = mountModal({ mood: { type: 'text', value: 'moody' } });
		await tick();

		let closeCount = 0;
		mounted.component.$on('close', () => {
			closeCount += 1;
		});

		buttonWithText('Cancel').click();
		await tick();

		expect(closeCount).toBe(1);
	});

	it('cancel reverts live edits to the variables the modal opened with', async () => {
		mounted = mountModal({ mood: { type: 'text', value: 'moody' } });
		await tick();

		const changes: VariablesMap[] = [];
		mounted.component.$on('change', (event: CustomEvent<VariablesMap>) => {
			changes.push(event.detail);
		});

		const input = document.querySelector<HTMLInputElement>('input[aria-label="Variable value"]');
		if (!input) throw new Error('value input not found');
		input.value = 'bright';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(changes.at(-1)?.mood).toMatchObject({ value: 'bright' });

		buttonWithText('Cancel').click();
		await tick();

		expect(changes.at(-1)).toEqual({ mood: { type: 'text', value: 'moody' } });
	});
});
