import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import PlanEditor from '../../src/routes/admin/components/plans/PlanEditor.svelte';
import type { Plan, PlanBody } from '../../src/lib/plans/types';
import { GB, KINDS, PLANS } from './plansFixtures';

const api = vi.hoisted(() => ({
	setGroupPlan: vi.fn(async () => undefined),
	setUserPlan: vi.fn(async () => undefined)
}));

vi.mock('../../src/lib/plans/api', () => api);

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

const DETAIL = {
	plan: PLANS[0],
	assigned_to: {
		groups: [{ id: 'g1', name: 'premium-tier-1', members: 3, is_default: false }],
		users: [{ id: 'u1', username: 'mira' }]
	},
	in_use: { people: 0, above_warn: 0, at_limit: 0, kinds: [] }
};
const GROUPS = [
	{ id: 'g1', name: 'premium-tier-1' },
	{ id: 'g2', name: 'staff' }
];
const USERS = [
	{ id: 'u1', username: 'mira', email: 'mira@example.com', account_type: 'USER' },
	{ id: 'u2', username: 'jonas', email: 'jonas@example.com', account_type: 'USER' }
];

function render(plan: Plan | null, onSave: (body: PlanBody) => void = () => {}, extra: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PlanEditor, { target, props: { plan, kinds: KINDS, onSave, onBack: () => {}, ...extra } } as never);
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

	describe('assignments', () => {
		function dialog() {
			return document.body.querySelector('[role="dialog"]') as HTMLElement;
		}

		function buttonNamed(root: ParentNode, text: string) {
			return Array.from(root.querySelectorAll('button')).find((b) => b.textContent?.trim().startsWith(text)) as HTMLButtonElement;
		}

		async function pickRow(name: string) {
			const row = Array.from(dialog().querySelectorAll<HTMLElement>('[role="row"]')).find((r) => r.textContent?.includes(name));
			if (!row) throw new Error(`row ${name} not found`);
			await click(row);
		}

		function renderAssigned(onAssignmentsChanged = vi.fn()) {
			const root = render(PLANS[0], () => {}, { detail: DETAIL, groups: GROUPS, users: USERS, onAssignmentsChanged });
			return { root, onAssignmentsChanged };
		}

		it('assigns the plan to the ticked users through the plans API and reloads', async () => {
			api.setUserPlan.mockClear();
			const { root, onAssignmentsChanged } = renderAssigned();
			await click(buttonNamed(root, 'Add users'));
			expect(dialog()).not.toBeNull();
			expect(dialog().textContent).toContain('jonas');
			expect(dialog().textContent).not.toContain('mira@example.com');
			await pickRow('jonas');
			await click(buttonNamed(dialog(), 'Add 1'));
			await vi.waitFor(() => expect(onAssignmentsChanged).toHaveBeenCalledTimes(1));
			expect(api.setUserPlan).toHaveBeenCalledTimes(1);
			expect(api.setUserPlan).toHaveBeenCalledWith('u2', PLANS[0].id);
		});

		it('assigns the plan to groups through the plans API', async () => {
			api.setGroupPlan.mockClear();
			const { root, onAssignmentsChanged } = renderAssigned();
			await click(buttonNamed(root, 'Add groups'));
			await pickRow('staff');
			await click(buttonNamed(dialog(), 'Add 1'));
			await vi.waitFor(() => expect(onAssignmentsChanged).toHaveBeenCalledTimes(1));
			expect(api.setGroupPlan).toHaveBeenCalledWith('g2', PLANS[0].id);
		});

		it('removes an assigned group and user by clearing their plan', async () => {
			api.setGroupPlan.mockClear();
			api.setUserPlan.mockClear();
			const { root, onAssignmentsChanged } = renderAssigned();
			await click(root.querySelector('button[aria-label="Remove premium-tier-1 from this plan"]'));
			await vi.waitFor(() => expect(onAssignmentsChanged).toHaveBeenCalledTimes(1));
			expect(api.setGroupPlan).toHaveBeenCalledWith('g1', null);
			await click(root.querySelector('button[aria-label="Remove mira from this plan"]'));
			await vi.waitFor(() => expect(onAssignmentsChanged).toHaveBeenCalledTimes(2));
			expect(api.setUserPlan).toHaveBeenCalledWith('u1', null);
		});

		it('shows the error when removing fails and does not reload', async () => {
			api.setGroupPlan.mockRejectedValueOnce(new Error('Group is locked'));
			const { root, onAssignmentsChanged } = renderAssigned();
			await click(root.querySelector('button[aria-label="Remove premium-tier-1 from this plan"]'));
			await vi.waitFor(() => expect(root.querySelector('[data-plan-assign-error]')?.textContent).toContain('Group is locked'));
			expect(onAssignmentsChanged).not.toHaveBeenCalled();
		});

		it('offers no assignment buttons while creating a plan', () => {
			const root = render(null);
			expect(buttonNamed(root, 'Add users')).toBeUndefined();
			expect(buttonNamed(root, 'Add groups')).toBeUndefined();
		});
	});
});
