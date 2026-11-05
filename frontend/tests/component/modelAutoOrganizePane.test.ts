import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import { mountComponent } from './helpers/mount';
import { instanceCatalogFixture, instanceRuleFixture } from './organizeFixtures';

type PageStore = Writable<{ url: URL }>;

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
const confirmDialog = vi.hoisted(() => vi.fn());

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, getClient: () => client, getModelById: vi.fn() } };
});
vi.mock('$lib/stores/confirm', () => ({ confirmDialog }));

const { page } = await import('$app/stores');
const { goto } = await import('$app/navigation');
const { default: Pane } = await import('../../src/lib/model-auto-organize/AutoOrganizePane.svelte');
const { AutoOrganizeController } = await import('../../src/lib/model-auto-organize/controller.svelte');

const pageStore = page as unknown as PageStore;
const BASE = '/api/admin/organize/instance';

function ok(data: unknown) {
	return Promise.resolve({ data: { success: true, data } });
}

const rules = () => [
	instanceRuleFixture(),
	instanceRuleFixture({
		id: 'ir2',
		name: 'Fetch CivitAI data',
		conditions: [{ fact: 'source', operator: 'is_not', value: 'civitai' }],
		actions: [{ action: 'civitai-provider.fetch_info', config: {} }],
		targets: { collections: [], groups: [] },
		filed_count: 31,
		waiting_count: 12,
		skipped_count: 3,
		updated_by_name: 'ola',
		last_run_at: '2026-11-05T09:00:00+00:00'
	}),
	instanceRuleFixture({
		id: 'ir3',
		name: 'Illustrious to illustrious',
		enabled: false,
		status: 'paused',
		paused_reason: 'group_missing',
		actions: [{ action: 'assign_model_group', config: { group_id: 'gone' } }],
		targets: { collections: [], groups: [{ id: 'gone', name: '', exists: false }] }
	})
];

async function settle() {
	for (let i = 0; i < 10; i++) {
		await new Promise((resolve) => setTimeout(resolve, 0));
		flushSync();
	}
}

let target: HTMLElement;
let ctrl: InstanceType<typeof AutoOrganizeController>;

function open(search = '') {
	pageStore.set({ url: new URL(`http://localhost/admin?tab=models&view=auto-organize${search}`) } as never);
}

function mountPane() {
	ctrl = new AutoOrganizeController();
	target = mountComponent(Pane, { ctrl }).target;
}

beforeEach(() => {
	for (const fn of [client.get, client.post, client.put, client.patch, client.delete, confirmDialog, goto as ReturnType<typeof vi.fn>]) {
		(fn as ReturnType<typeof vi.fn>).mockReset?.();
	}
	client.get.mockImplementation((url: string) => {
		if (url === `${BASE}/catalog`) return ok(instanceCatalogFixture);
		if (url === `${BASE}/rules`) return ok(rules());
		if (url === `${BASE}/activity`) return ok({ runs: [], next_before: null });
		if (url === `${BASE}/rules/ir1`) return ok(rules()[0]);
		return ok(null);
	});
	open();
});

describe('Admin Models Auto-organize pane', () => {
	it('reads the catalog and rules from the instance API only', async () => {
		mountPane();
		await settle();
		const called = client.get.mock.calls.map((c) => c[0]);
		expect(called).toContain(`${BASE}/catalog`);
		expect(called).toContain(`${BASE}/rules`);
		expect(called.every((u: string) => u.startsWith(BASE))).toBe(true);
		expect(client.get.mock.calls.find((c) => c[0] === `${BASE}/rules`)![1]).toEqual({ params: { subject: 'model' } });
		expect(ctrl.rules).toHaveLength(3);
	});

	it('shows who changed each rule, its waiting and skipped counts and the plain sentence', async () => {
		mountPane();
		await settle();
		const cards = [...target.querySelectorAll('[data-testid="rule-card"]')];
		expect(cards).toHaveLength(3);
		expect(cards[0].querySelector('[data-testid="rule-changed-by"]')!.textContent).toContain('Changed by mira');
		expect(cards[0].textContent).toContain('give krea-2 access');
		expect(cards[0].textContent).toContain('ignoring dashes and underscores');
		expect(cards[1].querySelector('[data-testid="rule-changed-by"]')!.textContent).toContain('Changed by ola');
		expect(cards[1].querySelector('[data-testid="rule-waiting"]')!.textContent).toContain('12 waiting');
		expect(cards[1].querySelector('[data-testid="rule-skipped"]')!.textContent).toContain('3 skipped');
		expect(cards[1].textContent).toContain('fetch CivitAI data');
		expect(cards[1].textContent).toContain('12 models are waiting');
		expect(cards[0].querySelector('[data-testid="rule-waiting"]')).toBeNull();
	});

	it('explains a rule paused because its user group is gone', async () => {
		mountPane();
		await settle();
		const paused = target.querySelector('[data-rule-id="ir3"]')!;
		expect(paused.textContent).toContain('Paused');
		expect(paused.textContent).toContain('Its user group is gone, so it was paused. Pick a group and switch it back on.');
		expect(target.textContent).toContain('One rule was paused or needs a look.');
	});

	it('says the rules belong to the instance and offers only the two instance templates', async () => {
		mountPane();
		await settle();
		expect(target.textContent).toContain('These rules belong to the instance');
		const titles = [...target.querySelectorAll('button')].map((b) => b.textContent ?? '');
		expect(titles.some((t) => t.includes('Open a family to a user group'))).toBe(true);
		expect(titles.some((t) => t.includes('Fetch CivitAI data for new models'))).toBe(true);
		expect(ctrl.templates).toHaveLength(2);
	});

	it('switches a rule on or off through the instance API', async () => {
		client.patch.mockImplementation(() => ok({ ...rules()[2], enabled: true, status: 'active', paused_reason: null }));
		mountPane();
		await settle();
		ctrl.toggle(ctrl.rules[2], true);
		await settle();
		expect(client.patch).toHaveBeenCalledWith(`${BASE}/rules/ir3`, { enabled: true });
		expect(ctrl.rules[2].enabled).toBe(true);
	});

	it('asks before deleting and only then calls the instance API', async () => {
		confirmDialog.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
		client.delete.mockImplementation(() => ok({ deleted: true }));
		mountPane();
		await settle();
		await ctrl.remove(ctrl.rules[0]);
		expect(client.delete).not.toHaveBeenCalled();
		await ctrl.remove(ctrl.rules[0]);
		expect(client.delete).toHaveBeenCalledWith(`${BASE}/rules/ir1`);
		expect(ctrl.rules.map((r) => r.id)).toEqual(['ir2', 'ir3']);
	});

	it('opens the editor for the rule named in the address, read from the instance API', async () => {
		open('&rule=ir1');
		mountPane();
		await settle();
		expect(ctrl.editor?.draft.id).toBe('ir1');
		const organizeCalls = client.get.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('organize'));
		expect(organizeCalls.length).toBeGreaterThan(0);
		expect(organizeCalls.every((u) => u.startsWith(BASE))).toBe(true);
		expect(target.querySelector('[data-action="assign_model_group"]')).not.toBeNull();
	});

	it('shows Activity from the instance API', async () => {
		open('&pane=activity');
		mountPane();
		await settle();
		expect(client.get.mock.calls.map((c) => c[0])).toContain(`${BASE}/activity`);
		expect(target.querySelector('[data-testid="rule-list"]')).toBeNull();
	});

	it('shows the empty state with a New rule button when there are no rules', async () => {
		client.get.mockImplementation((url: string) =>
			url === `${BASE}/catalog` ? ok(instanceCatalogFixture) : ok([])
		);
		mountPane();
		await settle();
		expect(target.textContent).toContain('No rules yet');
		expect([...target.querySelectorAll('button')].some((b) => b.textContent?.trim() === 'New rule')).toBe(true);
	});

	it('shows a retryable error when the rules cannot be loaded', async () => {
		client.get.mockImplementation((url: string) =>
			url === `${BASE}/catalog` ? ok(instanceCatalogFixture) : Promise.reject(new Error('offline'))
		);
		mountPane();
		await settle();
		expect(target.textContent).toContain('The rules could not be loaded.');
	});
});
