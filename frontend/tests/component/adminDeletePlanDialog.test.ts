import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import DeletePlanDialog from '../../src/routes/admin/components/plans/DeletePlanDialog.svelte';
import { PLANS, openSelectAndPick } from './plansFixtures';

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement | undefined;

const assigned = { groups: [{ id: 'g1', name: 'premium-tier-1' }], users: [{ id: 'u1', username: 'tomek' }] };

function render(onConfirm = vi.fn()) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(DeletePlanDialog as never, { target, props: { plan: PLANS[1], assigned, plans: PLANS, onConfirm, onClose: () => {} } } as never);
	flushSync();
	return onConfirm;
}

function button(text: string) {
	return Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) as HTMLButtonElement;
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('DeletePlanDialog', () => {
	it('lists what the plan is assigned to and excludes the plan itself from the picker', async () => {
		render();
		const list = document.body.querySelector('[data-delete-plan-assigned]')!.textContent;
		expect(list).toContain('premium-tier-1');
		expect(list).toContain('tomek');
		const trigger = document.body.querySelector('[data-reassign-select] button[aria-haspopup="listbox"]') as HTMLElement;
		trigger.click();
		await tick();
		flushSync();
		const labels = Array.from(document.body.querySelectorAll('[role="option"]')).map((o) => o.textContent?.trim());
		expect(labels.some((l) => l?.includes('Tier 1'))).toBe(false);
		expect(labels.some((l) => l?.includes('Free'))).toBe(true);
	});

	it('reassigns to the chosen plan and keeps the button disabled until one is chosen', async () => {
		const onConfirm = render();
		expect(button('Reassign and delete').disabled).toBe(true);
		await openSelectAndPick(document.body.querySelector('[data-reassign-select]') as HTMLElement, 'Free');
		expect(button('Reassign and delete').disabled).toBe(false);
		button('Reassign and delete').click();
		expect(onConfirm).toHaveBeenCalledWith('free');
	});

	it('clears assignments with reassign_to none', () => {
		const onConfirm = render();
		button('Clear assignments and delete').click();
		expect(onConfirm).toHaveBeenCalledWith('none');
	});
});
