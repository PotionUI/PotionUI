import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import PlansList from '../../src/routes/admin/components/plans/PlansList.svelte';
import type { Plan, PlansSettings } from '../../src/lib/plans/types';
import { GB, KINDS, PLANS } from './plansFixtures';

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

const settings: PlansSettings = { default_plan_id: 'free', exempt_admins: true, day_timezone: 'UTC', contact_line: 'Ask your admin for more.' };
const plans: Plan[] = [
	{ ...PLANS[0], is_default: true, assigned: { groups: 1, users: 0 }, usage: { members: 7, kinds: [{ kind: 'storage_bytes', used_total: 31.4 * GB }, { kind: 'generations_per_day', used_total: 96 }] } },
	{ ...PLANS[1], assigned: { groups: 1, users: 2 }, usage: { members: 3, kinds: [] } },
	PLANS[2]
];

function render(onSettings = vi.fn(), onOpen = vi.fn()) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PlansList, { target, props: { plans, kinds: KINDS, settings, onOpen, onSettings } });
	flushSync();
	return { root: target, onSettings, onOpen };
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('PlansList', () => {
	it('summarises limits per plan, marks the default and shows members and usage', () => {
		const { root } = render();
		const free = root.querySelector('[data-plan-card="free"]')!;
		expect(free.textContent).toContain('Default');
		expect(free.textContent).toContain('Storage 5 GB');
		expect(free.textContent).toContain('Generations 20 / day');
		expect(free.textContent).toContain('1 group - 0 users');
		expect(free.textContent).toContain('7 members');
		expect(free.textContent).toContain('31.4 GB - 96 today');
		const tier1 = root.querySelector('[data-plan-card="tier1"]')!;
		expect(tier1.textContent).not.toContain('Default');
		expect(tier1.textContent).toContain('1 group - 2 users');
		expect(root.querySelector('[data-plan-card="unlimited"]')!.textContent).toContain('No limits');
	});

	it('opens a plan from its card', async () => {
		const { root, onOpen } = render();
		(root.querySelector('[data-plan-card="tier1"]') as HTMLElement).click();
		expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'tier1' }));
	});

	it('toggles Admins are exempt through settings', async () => {
		const { root, onSettings } = render();
		const toggle = root.querySelector('input[role="switch"][aria-label="Admins are exempt"]') as HTMLInputElement;
		expect(toggle.checked).toBe(true);
		toggle.click();
		await tick();
		expect(onSettings).toHaveBeenCalledWith({ exempt_admins: false });
	});

	it('shows the editable contact line and saves it on change', async () => {
		const { root, onSettings } = render();
		await tick();
		flushSync();
		const input = root.querySelector('input[aria-label="Contact line"]') as HTMLInputElement;
		expect(input.value).toBe('Ask your admin for more.');
		input.value = 'Write to ops@example.com.';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(onSettings).toHaveBeenCalledWith({ contact_line: 'Write to ops@example.com.' });
	});

	it('gives the day timezone field room for long zone names', () => {
		const { root } = render();
		const input = root.querySelector('input[aria-label="Day timezone"]') as HTMLInputElement;
		expect(input.classList.contains('w-28')).toBe(false);
		expect(input.className).toMatch(/\bw-(5[6-9]|[6-9]\d|\[)/);
		expect(input.classList.contains('text-xs')).toBe(false);
	});
});
