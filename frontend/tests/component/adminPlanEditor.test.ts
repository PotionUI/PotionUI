import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import PlanEditor from '../../src/routes/admin/components/plans/PlanEditor.svelte';
import type { Plan, PlanBody } from '../../src/lib/plans/types';
import { GB, KINDS, PLANS } from './plansFixtures';

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

function render(plan: Plan | null, onSave: (body: PlanBody) => void = () => {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PlanEditor, { target, props: { plan, kinds: KINDS, onSave, onBack: () => {} } });
	flushSync();
	return target;
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

async function type(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	await tick();
	flushSync();
}

async function click(el: Element | null) {
	(el as HTMLElement).click();
	await tick();
	flushSync();
}

function saveButton(root: HTMLElement) {
	return Array.from(root.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Create' || b.textContent?.trim() === 'Save') as HTMLButtonElement;
}

describe('PlanEditor', () => {
	it('lists only the limits the plan has and offers the rest in the picker, plugin kinds tagged', async () => {
		const root = render(PLANS[0]);
		expect(Array.from(root.querySelectorAll('[data-limit-row]')).map((r) => r.getAttribute('data-limit-row'))).toEqual([
			'storage_bytes',
			'generations_per_day'
		]);
		await click(root.querySelector('[data-add-limit] button'));
		const options = Array.from(root.querySelectorAll('[data-kind-option]'));
		expect(options.map((o) => o.getAttribute('data-kind-option'))).toEqual(['cloud_spend_usd_month', 'example.credits']);
		expect(options[1].textContent).toContain('plugin');
		expect(options[0].textContent).not.toContain('plugin');
	});

	it('adds a limit from the picker and removes it again', async () => {
		const root = render(PLANS[0]);
		await click(root.querySelector('[data-add-limit] button'));
		await click(root.querySelector('[data-kind-option="cloud_spend_usd_month"]'));
		expect(root.querySelector('[data-limit-row="cloud_spend_usd_month"]')).not.toBeNull();
		expect(root.querySelector('[data-kind-picker]')).toBeNull();
		await click(root.querySelector('button[aria-label="Remove Cloud spend per month"]'));
		expect(root.querySelector('[data-limit-row="cloud_spend_usd_month"]')).toBeNull();
	});

	it('shows the admin-only mark on the cloud budget row only', async () => {
		const root = render({ ...PLANS[0], limits: [...PLANS[0].limits, { kind: 'cloud_spend_usd_month', value: 50 }] });
		expect(root.querySelector('[data-limit-row="cloud_spend_usd_month"] [data-admin-only-mark]')).not.toBeNull();
		expect(root.querySelector('[data-limit-row="storage_bytes"] [data-admin-only-mark]')).toBeNull();
	});

	it('saves the converted body from one Save', async () => {
		const onSave = vi.fn();
		const root = render(PLANS[0], onSave);
		expect(saveButton(root).disabled).toBe(true);
		await type(root.querySelector('[data-limit-input="storage_bytes"]') as HTMLInputElement, '20');
		await type(root.querySelector('[data-limit-input="generations_per_day"]') as HTMLInputElement, '100');
		await click(root.querySelector('[data-add-limit] button'));
		await click(root.querySelector('[data-kind-option="cloud_spend_usd_month"]'));
		await type(root.querySelector('[data-limit-input="cloud_spend_usd_month"]') as HTMLInputElement, '50');
		expect(root.textContent).toContain('1 unsaved change');
		await click(saveButton(root));
		expect(onSave).toHaveBeenCalledTimes(1);
		expect(onSave).toHaveBeenCalledWith({
			name: 'Free',
			description: '',
			limits: [
				{ kind: 'storage_bytes', value: 20 * GB },
				{ kind: 'generations_per_day', value: 100 },
				{ kind: 'cloud_spend_usd_month', value: 50 }
			]
		});
	});

	it('blocks Create for a new plan until it has a name and valid values', async () => {
		const onSave = vi.fn();
		const root = render(null, onSave);
		expect(saveButton(root).disabled).toBe(true);
		await type(root.querySelector('#plan-name') as HTMLInputElement, 'Tier 3');
		expect(saveButton(root).disabled).toBe(false);
		await click(root.querySelector('[data-add-limit] button'));
		await click(root.querySelector('[data-kind-option="storage_bytes"]'));
		expect(saveButton(root).disabled).toBe(true);
		await type(root.querySelector('[data-limit-input="storage_bytes"]') as HTMLInputElement, '1.5');
		expect(saveButton(root).disabled).toBe(false);
		await click(saveButton(root));
		expect(onSave).toHaveBeenCalledWith({ name: 'Tier 3', description: '', limits: [{ kind: 'storage_bytes', value: 1.5 * GB }] });
	});

	it('discard restores the saved limits', async () => {
		const root = render(PLANS[0]);
		await click(root.querySelector('button[aria-label="Remove Storage space"]'));
		expect(root.querySelector('[data-limit-row="storage_bytes"]')).toBeNull();
		const discard = Array.from(root.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Discard') as HTMLButtonElement;
		await click(discard);
		expect(root.querySelector('[data-limit-row="storage_bytes"]')).not.toBeNull();
	});
});
