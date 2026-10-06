import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import GroupPlanSection from '../../src/routes/admin/components/plans/GroupPlanSection.svelte';
import UserPlanSection from '../../src/routes/admin/components/plans/UserPlanSection.svelte';
import type { UserPlanDetail } from '../../src/lib/plans/types';
import { GB, IMPACT, KINDS, PLANS, USER_DETAIL } from './plansFixtures';

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

function render(Component: never, props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(Component, { target, props } as never);
	flushSync();
	return target;
}

async function settle() {
	await tick();
	flushSync();
}

async function pickPlan(container: HTMLElement, name: string) {
	(container.querySelector('[data-plan-picker-trigger]') as HTMLElement).click();
	await settle();
	const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement;
	expect(dialog).not.toBeNull();
	const row = Array.from(dialog.querySelectorAll<HTMLElement>('[role="row"]')).find((r) => r.textContent?.includes(name));
	if (!row) throw new Error(`plan row ${name} not found`);
	row.click();
	await settle();
	const apply = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent?.trim().startsWith('Select ')) as HTMLButtonElement;
	apply.click();
	await settle();
}

function inheritButton(container: HTMLElement) {
	return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Use inherited plan')) as HTMLButtonElement | undefined;
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('GroupPlanSection', () => {
	it('renders the per member impact table with changes, kept values and over-limit users', () => {
		const root = render(GroupPlanSection as never, { plans: PLANS, kinds: KINDS, planId: 'tier1', impact: IMPACT, onChange: () => {} });
		const rows = Array.from(root.querySelectorAll('[data-impact-row]'));
		expect(rows.map((r) => r.getAttribute('data-impact-row'))).toEqual(['jonas', 'mira', 'lena']);
		const headers = Array.from(root.querySelectorAll('thead th')).map((th) => th.textContent?.trim());
		expect(headers).toEqual(['Member', 'Storage', 'Generations', 'Note']);
		const jonas = rows[0];
		expect(jonas.querySelector('[data-impact-cell="storage_bytes"]')?.textContent?.trim()).toBe('5 GB -> 20 GB');
		expect(jonas.querySelector('[data-impact-cell="generations_per_day"]')?.textContent?.trim()).toBe('20 -> 100');
		expect(rows[1].querySelector('[data-impact-cell="storage_bytes"]')?.textContent?.trim()).toBe('100 GB (kept)');
		expect(rows[1].textContent).toContain('bigger from premium-tier-2');
		expect(rows[2].querySelector('[data-impact-cell="storage_bytes"]')?.textContent).toContain('over');
		expect(root.textContent).toContain('What changes for the 3 members');
	});

	it('says nothing changes for a group without members', () => {
		const root = render(GroupPlanSection as never, {
			plans: PLANS,
			kinds: KINDS,
			planId: null,
			impact: { ...IMPACT, members: [], kinds: [], summary: { members: 0, changed: 0, over_after: 0 } },
			onChange: () => {}
		});
		expect(root.querySelector('[data-plan-impact]')).toBeNull();
		expect(root.querySelector('[data-plan-impact-empty]')).not.toBeNull();
	});

	it('shows the inherited default on the trigger and reports the picked plan', async () => {
		const onChange = vi.fn();
		const root = render(GroupPlanSection as never, { plans: PLANS, kinds: KINDS, planId: null, defaultPlanName: 'Free', onChange });
		const select = root.querySelector('[data-group-plan-select]') as HTMLElement;
		expect(select.querySelector('[data-plan-picker-label]')?.textContent).toContain('Inherit (use default: Free)');
		expect(inheritButton(select)).toBeUndefined();
		await pickPlan(select, 'Tier 1');
		expect(onChange).toHaveBeenLastCalledWith('tier1');
		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
	});

	it('shows the current plan with its limits and clears it back to inherit', async () => {
		const onChange = vi.fn();
		const root = render(GroupPlanSection as never, { plans: PLANS, kinds: KINDS, planId: 'tier1', onChange });
		const select = root.querySelector('[data-group-plan-select]') as HTMLElement;
		expect(select.querySelector('[data-plan-picker-label]')?.textContent).toBe('Tier 1');
		expect(select.querySelector('[data-plan-picker-summary]')?.textContent).toBeTruthy();
		inheritButton(select)!.click();
		await settle();
		expect(onChange).toHaveBeenLastCalledWith(null);
	});
});

describe('UserPlanSection', () => {
	it('shows each effective limit with its source and usage', () => {
		const root = render(UserPlanSection as never, { plans: PLANS, kinds: KINDS, detail: USER_DETAIL, overrideId: null, onChange: () => {} });
		expect(root.querySelector('[data-effective-plan]')?.textContent).toContain('Tier 1');
		expect(root.querySelector('[data-effective-plan]')?.textContent).toContain('premium-tier-1');
		const storage = root.querySelector('[data-effective-limit="storage_bytes"]')!;
		expect(storage.querySelector('[data-limit-source]')?.textContent).toBe('Tier 1 via premium-tier-1; All users says 5 GB, smaller');
		expect(storage.textContent).toContain('18.6 GB');
		expect(storage.textContent).toContain('20 GB');
		expect(storage.querySelector('[data-usage-state="warn"]')).not.toBeNull();
		expect(storage.textContent).toContain('80%+');
		expect(root.querySelector('[data-effective-limit="generations_per_day"]')?.textContent).not.toContain('80%+');
		const daily = root.querySelector('[data-effective-limit="generations_per_day"]')!;
		expect(daily.querySelector('[data-usage-state="ok"]')).not.toBeNull();
		expect(daily.textContent).toContain('37');
	});

	it('marks an admin exempt and an override source', () => {
		const exempt: UserPlanDetail = {
			...USER_DETAIL,
			exempt: true,
			plan: null,
			limits: [{ ...USER_DETAIL.limits[0], used: 212 * GB, limit: null, percent: null, enforced: false, detail: undefined }]
		};
		const root = render(UserPlanSection as never, { plans: PLANS, kinds: KINDS, detail: exempt, overrideId: null, onChange: () => {} });
		expect(root.querySelector('[data-limit-source]')?.textContent).toBe('Admin, exempt from limits');
		expect(root.querySelector('[data-effective-limit] [data-usage-state="none"]')).not.toBeNull();
	});

	it('reports the personal override and clears it back to groups', async () => {
		const onChange = vi.fn();
		const root = render(UserPlanSection as never, { plans: PLANS, kinds: KINDS, detail: USER_DETAIL, overrideId: null, onChange });
		const select = root.querySelector('[data-user-plan-select]') as HTMLElement;
		expect(select.querySelector('[data-plan-picker-label]')?.textContent).toContain('None (use groups)');
		expect(inheritButton(select)).toBeUndefined();
		await pickPlan(select, 'Unlimited');
		expect(onChange).toHaveBeenLastCalledWith('unlimited');
		unmount(component!);
		component = null;
		target?.remove();
		const again = render(UserPlanSection as never, { plans: PLANS, kinds: KINDS, detail: USER_DETAIL, overrideId: 'unlimited', onChange });
		const againSelect = again.querySelector('[data-user-plan-select]') as HTMLElement;
		expect(againSelect.querySelector('[data-plan-picker-label]')?.textContent).toContain('Unlimited');
		inheritButton(againSelect)!.click();
		await settle();
		expect(onChange).toHaveBeenLastCalledWith(null);
	});
});
