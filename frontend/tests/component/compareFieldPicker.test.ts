// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { buildAxisCandidates } from '$lib/generation/compare/candidates';

const { default: FieldPicker } = await import('$lib/components/compare/FieldPicker.svelte');

const schema = {
	properties: {
		root: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					label: 'Generation',
					children: [
						{
							type: 'section',
							label: 'Sampling',
							children: [
								{
									name: 'sampler',
									type: 'select',
									title: 'Sampler',
									options: [
										{ value: 'euler', label: 'euler' },
										{ value: 'heun', label: 'heun' }
									]
								},
								{
									name: 'scheduler',
									type: 'select',
									title: 'Scheduler',
									options: [
										{ value: 'simple', label: 'simple' },
										{ value: 'beta', label: 'beta' }
									]
								},
								{ name: 'steps', type: 'slider', title: 'Steps', minimum: 1, maximum: 60 },
								{ name: 'refiner', type: 'checkbox', title: 'Refiner' },
								{
									name: 'refiner_steps',
									type: 'number',
									title: 'Refiner steps',
									reactions: [{ when: { field: 'refiner', equals: false }, then: { set_visibility: false } }]
								}
							]
						}
					]
				}
			]
		}
	}
};

const candidates = buildAxisCandidates(schema, { refiner: false, steps: 20 });

let instance: ReturnType<typeof mount> | undefined;
let anchor: HTMLButtonElement | undefined;

function mountPicker(overrides: Record<string, unknown> = {}) {
	anchor = document.createElement('button');
	document.body.appendChild(anchor);
	const props = {
		candidates,
		selectedField: null as string | null,
		otherField: null as string | null,
		otherSlot: 'y' as 'x' | 'y',
		anchor,
		onSelect: vi.fn(),
		onClose: vi.fn(),
		...overrides
	};
	instance = mount(FieldPicker, { target: document.body, props });
	flushSync();
	return props;
}

function options(): HTMLButtonElement[] {
	return [...document.querySelectorAll<HTMLButtonElement>('[role="option"]')];
}

function optionFor(field: string): HTMLButtonElement {
	return document.querySelector<HTMLButtonElement>(`[data-field="${field}"]`)!;
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	anchor?.remove();
	document.body.innerHTML = '';
});

describe('FieldPicker', () => {
	it('groups fields by section and ends with the unavailable group', () => {
		mountPicker();
		const text = document.body.textContent ?? '';
		expect(text).toContain('Sampling');
		expect(text).toContain('Prompt');
		expect(text).toContain('Unavailable now');
		expect(text.indexOf('Sampling')).toBeLessThan(text.indexOf('Unavailable now'));
		expect(optionFor('sampler').textContent).toContain('select');
	});

	it('shows the reaction reason on unavailable rows and does not select them', () => {
		const props = mountPicker();
		const row = optionFor('refiner_steps');
		expect(row.disabled).toBe(true);
		expect(row.textContent).toContain('Hidden while Refiner is off');
		row.click();
		flushSync();
		expect(props.onSelect).not.toHaveBeenCalled();
	});

	it('filters by the search text', () => {
		mountPicker();
		const input = document.querySelector<HTMLInputElement>('input[aria-label="Find a field"]')!;
		input.value = 'sched';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(options().map((o) => o.dataset.field)).toEqual(['scheduler']);
	});

	it('disables the field that is already on the other axis', () => {
		const props = mountPicker({ otherField: 'steps', otherSlot: 'x' });
		const row = optionFor('steps');
		expect(row.disabled).toBe(true);
		expect(row.textContent).toContain('On the X axis');
		row.click();
		expect(props.onSelect).not.toHaveBeenCalled();
	});

	it('calls back with the picked candidate', () => {
		const props = mountPicker();
		optionFor('scheduler').click();
		flushSync();
		expect(props.onSelect).toHaveBeenCalledTimes(1);
		expect(props.onSelect.mock.calls[0][0].field).toBe('scheduler');
	});

	it('selects the active row with Enter and closes with Escape', () => {
		const props = mountPicker({ selectedField: 'sampler' });
		const panel = document.querySelector<HTMLElement>('[role="dialog"]')!;
		panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		flushSync();
		panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		expect(props.onSelect.mock.calls[0][0].field).toBe('scheduler');
		panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		expect(props.onClose).toHaveBeenCalled();
	});

	it('shows the open-a-preset empty state without candidates', () => {
		mountPicker({ candidates: [] });
		expect(document.body.textContent).toContain('Open a preset first');
	});
});
