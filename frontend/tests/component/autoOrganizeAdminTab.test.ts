// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { OrganizeAdminOverview } from '$lib/types/organize';

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return {
		...actual,
		api: {
			...actual.api,
			getOrganizeAdminOverview: vi.fn(),
			updateOrganizeAdminControls: vi.fn(),
			updateOrganizeAdminUser: vi.fn(),
			getToken: vi.fn(() => null)
		}
	};
});
vi.mock('$lib/stores/confirm', async () => {
	const actual = await vi.importActual<typeof import('$lib/stores/confirm')>('$lib/stores/confirm');
	return { ...actual, confirmDialog: vi.fn() };
});

const api = (await import('$lib/services/api')).api;
const { confirmDialog } = await import('$lib/stores/confirm');
const { default: AutoOrganizeTab } = await import('../../src/routes/admin/components/AutoOrganizeTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function overview(overrides: Partial<OrganizeAdminOverview> = {}): OrganizeAdminOverview {
	return {
		paused_all: false,
		default_rule_cap: 50,
		hourly_limit: 200,
		totals: {
			users_with_rules: 3,
			rules: 9,
			enabled_rules: 7,
			paused_rules: 1,
			items_filed_24h: 412,
			items_filed_total: 5120,
			running_jobs: 0
		},
		users: [
			{
				user_id: 'u1',
				username: 'jan',
				rules: 4,
				enabled_rules: 3,
				paused_rules: 1,
				items_filed_24h: 120,
				items_filed_total: 2048,
				paused: false,
				rule_cap: null,
				effective_rule_cap: 50,
				last_run_at: '2026-10-05T09:12:00+00:00'
			}
		],
		...overrides
	};
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: AutoOrganizeTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function switchByLabel(root: HTMLElement, label: string): HTMLElement {
	const el = root.querySelector(`[aria-label="${label}"]`);
	if (!el) throw new Error(`no control labelled ${label}`);
	return el as HTMLElement;
}

async function goTo(root: HTMLElement, label: string) {
	const row = Array.from(root.querySelectorAll('[role="listbox"] > *')).find((b) => b.textContent?.includes(label));
	if (!row) throw new Error(`no section ${label}`);
	(row as HTMLElement).click();
	await settle();
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('AutoOrganizeTab', () => {
	it('shows totals and people', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data: overview() });
		mounted = mount();
		await settle();
		expect(mounted.target.textContent).toContain('412');
		expect(mounted.target.textContent).toContain('Pause Auto-organize for everyone');
		await goTo(mounted.target, 'People');
		expect(mounted.target.textContent).toContain('jan');
	});

	it('asks before pausing everyone and sends the switch', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(confirmDialog).mockResolvedValue(true);
		vi.mocked(api.updateOrganizeAdminControls).mockResolvedValue({ success: true, data: overview({ paused_all: true }) });
		mounted = mount();
		await settle();
		switchByLabel(mounted.target, 'Pause Auto-organize for everyone').click();
		await settle();
		expect(confirmDialog).toHaveBeenCalledTimes(1);
		expect(api.updateOrganizeAdminControls).toHaveBeenCalledWith({ paused_all: true });
	});

	it('does nothing when the confirmation is declined', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(confirmDialog).mockResolvedValue(false);
		mounted = mount();
		await settle();
		switchByLabel(mounted.target, 'Pause Auto-organize for everyone').click();
		await settle();
		expect(api.updateOrganizeAdminControls).not.toHaveBeenCalled();
	});

	it('pauses one person without a confirmation', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(api.updateOrganizeAdminUser).mockResolvedValue({
			success: true,
			data: { ...overview().users[0], paused: true }
		});
		mounted = mount();
		await settle();
		await goTo(mounted.target, 'People');
		switchByLabel(mounted.target, 'Pause filing for jan').click();
		await settle();
		expect(api.updateOrganizeAdminUser).toHaveBeenCalledWith('u1', { paused: true });
	});

	it('saves only the changed limit', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data: overview() });
		vi.mocked(api.updateOrganizeAdminControls).mockResolvedValue({
			success: true,
			data: overview({ hourly_limit: 300 })
		});
		mounted = mount();
		await settle();
		await goTo(mounted.target, 'Limits');
		const inputs = Array.from(mounted.target.querySelectorAll('input')).filter((i) => i.value === '200');
		expect(inputs).toHaveLength(1);
		inputs[0].value = '300';
		inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		const save = Array.from(mounted.target.querySelectorAll('button')).find((b) => b.textContent?.includes('Save'));
		save?.click();
		await settle();
		expect(api.updateOrganizeAdminControls).toHaveBeenCalledWith({ hourly_limit: 300 });
	});

	it('shows a load error for a 403', async () => {
		vi.mocked(api.getOrganizeAdminOverview).mockRejectedValue({ response: { status: 403 } });
		mounted = mount();
		await settle();
		expect(mounted.target.textContent).toContain('Only administrators');
	});
});

describe('AutoOrganizeTab people list', () => {
	it('filters people by name', async () => {
		const data = overview();
		data.users.push({ ...data.users[0], user_id: 'u2', username: 'marta' });
		vi.mocked(api.getOrganizeAdminOverview).mockResolvedValue({ success: true, data });
		mounted = mount();
		await settle();
		await goTo(mounted.target, 'People');
		const search = mounted.target.querySelector('input[placeholder="Search by name…"]') as HTMLInputElement;
		search.value = 'mar';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(mounted.target.textContent).toContain('marta');
		expect(mounted.target.textContent).not.toContain('jan');
	});
});
