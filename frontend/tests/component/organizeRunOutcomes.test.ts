import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mountComponent } from './helpers/mount';
import { advanceTimers } from './helpers/settle';
import { instanceRunFixture } from './organizeFixtures';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
const confirmDialog = vi.hoisted(() => vi.fn());
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, getClient: () => client } };
});
vi.mock('$lib/stores/confirm', () => ({ confirmDialog }));
vi.mock('$lib/stores/toast', () => ({ toasts }));

const { default: Host } = await import('./stubs/OrganizeScopeHost.svelte');
const { default: ActivityList } = await import('../../src/lib/components/organize/ActivityList.svelte');

const BASE = '/api/admin/organize/instance';

function ok(data: unknown) {
	return Promise.resolve({ data: { success: true, data } });
}

function itemsPage(status: string | undefined, page: number) {
	const make = (name: string, st: string, detail: string, attempts = 0) => ({
		item_type: 'model',
		item_id: `${st}-${name}`,
		name,
		model_type: 'lora',
		status: st,
		code: st === 'updated' ? 'civitai-provider.fetch_info' : st === 'waiting' ? 'unreachable' : 'hash_mismatch',
		detail,
		attempts,
		next_try_at: null
	});
	const all = [
		make('style_a', 'updated', 'CivitAI'),
		make('mystery_lora', 'skipped', 'CivitAI answered with a different file, so the answer was ignored'),
		make('detail_xl', 'waiting', 'CivitAI could not be reached', 2)
	];
	const rows = status ? all.filter((r) => r.status === status) : all;
	return { run: instanceRunFixture(), items: rows, total: status ? { updated: 19, skipped: 3, waiting: 9 }[status as 'updated'] : 31, page, page_size: 25 };
}

let target: HTMLElement;

async function settle() {
	for (let i = 0; i < 8; i++) await advanceTimers(0);
}

function mountActivity(scope: 'instance' | 'user' = 'instance') {
	target = mountComponent(Host, { component: ActivityList, scope, props: { subject: 'model' } }).target;
}

function button(root: ParentNode, label: string): HTMLButtonElement {
	const found = [...root.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith(label));
	if (!found) throw new Error(`no button ${label}`);
	return found as HTMLButtonElement;
}

function dialog(): HTMLElement {
	return document.body.querySelector('[data-testid="run-details"]') as HTMLElement;
}

beforeEach(() => {
	vi.useFakeTimers();
	for (const fn of [client.get, client.post, client.put, client.patch, client.delete, confirmDialog, ...Object.values(toasts)]) fn.mockReset();
	client.get.mockImplementation((url: string, config?: { params?: { status?: string; page?: number } }) => {
		if (url === `${BASE}/activity`) return ok({ runs: [instanceRunFixture()], next_before: null });
		if (url === `${BASE}/runs/run-civ/items`) return ok(itemsPage(config?.params?.status, config?.params?.page ?? 1));
		return ok(null);
	});
});

afterEach(() => {
	vi.useRealTimers();
});

describe('instance Activity', () => {
	it('shows the outcome reasons, who started the run and the Details button', async () => {
		mountActivity();
		await settle();
		const outcomes = target.querySelector('[data-testid="run-outcomes"]')!.textContent!;
		expect(outcomes).toContain('9 waiting: CivitAI could not be reached');
		expect(outcomes).toContain('3 skipped: CivitAI answered with a different file');
		expect(target.textContent).toContain('by ola');
		expect(target.textContent).toContain('Fetched CivitAI data for 19 models');
		expect(target.textContent).toContain('Existing models');
		expect(client.get.mock.calls[0][0]).toBe(`${BASE}/activity`);
		const labels = [...target.querySelectorAll('button')].map((b) => b.textContent?.trim());
		expect(labels).toContain('Details');
		expect(labels).not.toContain('Undo');
	});

	it('has no Details button for a run where everything was updated', async () => {
		client.get.mockImplementation(() =>
			ok({ runs: [instanceRunFixture({ outcomes: { updated: 19, skipped: 0, waiting: 0 }, reasons: [] })], next_before: null })
		);
		mountActivity();
		await settle();
		expect(target.querySelector('[data-testid="run-outcomes"]')).toBeNull();
		expect([...target.querySelectorAll('button')].map((b) => b.textContent?.trim())).not.toContain('Details');
	});

	it('asks before undoing a group run and says the group stays', async () => {
		const run = instanceRunFixture({
			id: 'run-grp',
			rule_name: 'Krea-2 models to krea-2',
			can_undo: true,
			outcomes: { updated: 14, skipped: 0, waiting: 0 },
			reasons: [],
			changes: { collections: [], tags: [], groups: [{ id: 'g1', name: 'krea-2', count: 14, live: 14 }], other: [] }
		});
		client.get.mockImplementation(() => ok({ runs: [run], next_before: null }));
		client.post.mockImplementation(() => ok({ undone: 14, items: 14, skipped: 0, run: { ...run, can_undo: false, status: 'undone' } }));
		confirmDialog.mockResolvedValue(true);
		mountActivity();
		await settle();
		expect(target.textContent).toContain('Gave krea-2 access to 14 models');
		button(target, 'Undo').click();
		await settle();
		expect(confirmDialog).toHaveBeenCalledTimes(1);
		expect(confirmDialog.mock.calls[0][0].message).toContain('the group stays');
		expect(client.post).toHaveBeenCalledWith(`${BASE}/runs/run-grp/undo`);
		expect(toasts.success).toHaveBeenCalledWith('Undid this run for 14 models');
	});

	it('does not undo when the confirmation is declined', async () => {
		const run = instanceRunFixture({ can_undo: true, id: 'run-grp' });
		client.get.mockImplementation(() => ok({ runs: [run], next_before: null }));
		confirmDialog.mockResolvedValue(false);
		mountActivity();
		await settle();
		button(target, 'Undo').click();
		await settle();
		expect(client.post).not.toHaveBeenCalled();
	});
});

describe('run details', () => {
	async function openDetails() {
		mountActivity();
		await settle();
		button(target, 'Details').click();
		await settle();
	}

	it('lists every outcome with counts on the filter and a retry button for the waiting ones', async () => {
		await openDetails();
		const root = dialog();
		expect(root).not.toBeNull();
		expect(root.textContent).toContain('All 31');
		expect(root.textContent).toContain('Updated 19');
		expect(root.textContent).toContain('Skipped 3');
		expect(root.textContent).toContain('Waiting 9');
		expect(root.querySelectorAll('[data-testid="run-item"]')).toHaveLength(3);
		expect(root.textContent).toContain('Try the 9 waiting again now');
		expect(root.textContent).toContain('Showing 1-25 of 31');
		expect(root.querySelector('nav[aria-label="Pagination"]')).not.toBeNull();
	});

	it('filters by outcome through the status query and goes back to page one', async () => {
		await openDetails();
		button(dialog(), 'Skipped').click();
		await settle();
		const last = client.get.mock.calls.at(-1)!;
		expect(last[0]).toBe(`${BASE}/runs/run-civ/items`);
		expect(last[1].params).toEqual({ status: 'skipped', page: 1, page_size: 25 });
		const rows = [...dialog().querySelectorAll('[data-testid="run-item"]')];
		expect(rows).toHaveLength(1);
		expect(rows[0].getAttribute('data-outcome')).toBe('skipped');
		expect(rows[0].textContent).toContain('different file');
		expect(rows[0].textContent).toContain('mystery_lora');
	});

	it('pages with the numeric pagination', async () => {
		await openDetails();
		(dialog().querySelector('button[aria-label="Page 2"]') as HTMLButtonElement).click();
		await settle();
		expect(client.get.mock.calls.at(-1)![1].params).toEqual({ page: 2, page_size: 25 });
		expect(dialog().textContent).toContain('Showing 26-31 of 31');
	});

	it('shows the waiting item with its attempts', async () => {
		await openDetails();
		button(dialog(), 'Waiting').click();
		await settle();
		const row = dialog().querySelector('[data-outcome="waiting"]')!;
		expect(row.textContent).toContain('detail_xl');
		expect(row.textContent).toContain('CivitAI could not be reached');
		expect(row.textContent).toContain('try 2');
	});

	it('tries the waiting ones again and closes the dialog', async () => {
		client.post.mockImplementation(() => ok({ id: 'job1', status: 'queued', total: 9 }));
		await openDetails();
		button(dialog(), 'Try the 9 waiting again now').click();
		await settle();
		expect(client.post).toHaveBeenCalledWith(`${BASE}/runs/run-civ/retry`);
		expect(toasts.success).toHaveBeenCalledWith('Trying 9 models again');
		expect(dialog()).toBeNull();
	});

	it('says nothing is waiting when the server answers 409 nothing_waiting', async () => {
		client.post.mockImplementation(() =>
			Promise.reject(
				Object.assign(new Error('conflict'), {
					isAxiosError: true,
					response: { status: 409, data: { detail: { error: 'nothing_waiting', message: 'Nothing is waiting in this run' } } }
				})
			)
		);
		await openDetails();
		button(dialog(), 'Try the 9 waiting again now').click();
		await settle();
		expect(toasts.info).toHaveBeenCalledWith('Nothing is waiting in this run any more.');
		expect(toasts.error).not.toHaveBeenCalled();
		expect(dialog()).not.toBeNull();
		expect([...dialog().querySelectorAll('button')].some((b) => b.textContent?.includes('waiting again now'))).toBe(false);
	});

	it('reports other retry failures with the server message', async () => {
		client.post.mockImplementation(() =>
			Promise.reject(
				Object.assign(new Error('x'), {
					isAxiosError: true,
					response: { status: 422, data: { detail: { error: 'rule_not_runnable', message: 'Fix what this rule needs before applying it' } } }
				})
			)
		);
		await openDetails();
		button(dialog(), 'Try the 9 waiting again now').click();
		await settle();
		expect(toasts.error).toHaveBeenCalledWith('Fix what this rule needs before applying it');
	});
});
