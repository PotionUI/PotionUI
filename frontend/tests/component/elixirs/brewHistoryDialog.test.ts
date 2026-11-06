import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const listRuns = vi.fn();
const cancelRuns = vi.fn();
const toastError = vi.fn();

vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => ({ listRuns, cancelRuns }) }));
vi.mock('$lib/stores/toast', () => ({ toasts: { error: (...args: unknown[]) => toastError(...args), show: vi.fn() } }));

import BrewHistoryDialog from '$lib/components/elixirs/BrewHistoryDialog.svelte';

function run(number: number, status = 'completed', over: Record<string, unknown> = {}) {
	return {
		id: `r${number}`,
		number,
		scope: 'elixir',
		scope_node_id: null,
		scope_label: null,
		run_mode: 'review',
		status,
		error: null,
		elixir_revision: 3,
		steps_done: 2,
		steps_total: 5,
		outputs: 7,
		created_at: '2026-11-04T10:00:00Z',
		updated_at: '2026-11-04T10:05:00Z',
		finished_at: status === 'completed' ? '2026-11-04T10:05:00Z' : null,
		...over
	};
}

function page(runs: ReturnType<typeof run>[], extra: Record<string, unknown> = {}) {
	return { ok: true, data: { runs, total: runs.length, active: 0, limit: 50, offset: 0, ...extra } };
}

let instance: ReturnType<typeof mount> | undefined;
const onopen = vi.fn();

async function open(props: Record<string, unknown> = {}) {
	instance = mount(BrewHistoryDialog as never, {
		target: document.body,
		props: { elixirId: 'e1', elixirName: 'Portrait', onclose: vi.fn(), onopen, ...props }
	});
	flushSync();
	await vi.waitFor(() => expect(listRuns).toHaveBeenCalled());
	await settle();
}

async function settle() {
	await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

const rows = () => [...document.body.querySelectorAll<HTMLElement>('[data-testid="brew-row"]')];
const byText = (text: string) =>
	[...document.body.querySelectorAll<HTMLElement>('button, [role="radio"], [role="option"]')].find((node) => node.textContent?.replace(/\s+/g, ' ').trim() === text);
const confirmButton = (label: string) =>
	[...document.body.querySelectorAll<HTMLElement>('button')].find((node) => node.textContent?.replace(/\s+/g, ' ').trim().startsWith(label) && node.textContent.includes('Enter'));
const lastQuery = () => listRuns.mock.calls[listRuns.mock.calls.length - 1][1];

async function click(node: HTMLElement | null | undefined) {
	expect(node).toBeTruthy();
	node!.click();
	flushSync();
	await settle();
}

beforeEach(() => {
	Element.prototype.scrollTo = vi.fn();
	listRuns.mockReset();
	cancelRuns.mockReset();
	toastError.mockReset();
	onopen.mockReset();
	cancelRuns.mockResolvedValue({ ok: true, data: { cancelled: [] } });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('BrewHistoryDialog', () => {
	it('loads the first page of 50 and shows number, steps and outputs', async () => {
		listRuns.mockResolvedValue(page([run(2), run(1)]));
		await open();
		expect(lastQuery()).toEqual({ limit: 50, offset: 0 });
		expect(rows().map((row) => row.dataset.number)).toEqual(['2', '1']);
		expect(rows()[0].textContent).toContain('Brew 2');
		expect(rows()[0].textContent).toContain('2/5');
		expect(document.body.querySelector('[data-testid="result-count"]')?.textContent).toContain('2 brews');
	});

	it('translates filters into request params and returns to the first page', async () => {
		listRuns.mockResolvedValue(page([run(1)]));
		await open();
		await click(byText('Active'));
		expect(lastQuery()).toMatchObject({ status: 'active', limit: 50, offset: 0 });
		await click(byText('7 days'));
		expect(lastQuery().from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
		expect(lastQuery().to).toBeUndefined();
		await click(byText('Today'));
		expect(lastQuery().from).toBe(lastQuery().to);
		await click(byText('Any time'));
		expect(lastQuery().from).toBeUndefined();
		await click(byText('All'));
		expect(lastQuery().status).toBeUndefined();
	});

	it('sends the custom date range, run mode, scope and search', async () => {
		listRuns.mockResolvedValue(page([run(1)]));
		await open();
		await click(byText('Custom'));
		const from = document.body.querySelector<HTMLInputElement>('input[aria-label="From date"]')!;
		from.value = '2026-10-01';
		from.dispatchEvent(new Event('input', { bubbles: true }));
		const to = document.body.querySelector<HTMLInputElement>('input[aria-label="To date"]')!;
		to.value = '2026-10-31';
		to.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(lastQuery()).toMatchObject({ from: '2026-10-01', to: '2026-10-31' });

		await click(document.body.querySelector<HTMLElement>('button[aria-label="Run mode"]'));
		await click(byText('Runs all branches'));
		expect(lastQuery().run_mode).toBe('automatic');
		await click(document.body.querySelector<HTMLElement>('button[aria-label="Scope"]'));
		await click(byText('Single step'));
		expect(lastQuery().scope).toBe('node');

		const search = document.body.querySelector<HTMLInputElement>('input[aria-label="Search brews"]')!;
		search.value = 'upscale';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(lastQuery().q).toBe('upscale'));
	});

	it('starts with the search the editor asks for', async () => {
		listRuns.mockResolvedValue(page([run(41)]));
		await open({ initialQuery: '41' });
		expect(listRuns.mock.calls[0][1].q).toBe('41');
	});

	it('pages with numeric pagination, offset = (page - 1) * 50', async () => {
		listRuns.mockResolvedValue(page([run(1)], { total: 120 }));
		await open();
		expect(document.body.querySelector('[data-testid="paging"]')?.textContent).toContain('Showing 1-50 of 120');
		await click(byText('2'));
		expect(lastQuery()).toMatchObject({ limit: 50, offset: 50 });
		expect(document.body.querySelector('[data-testid="paging"]')?.textContent).toContain('Showing 51-100 of 120');
		await click(byText('3'));
		expect(lastQuery().offset).toBe(100);
	});

	it('stops one running brew from its row without a confirm', async () => {
		listRuns.mockResolvedValue(page([run(2, 'running'), run(1)], { active: 1 }));
		await open();
		expect(document.body.querySelectorAll('button[aria-label="Stop Brew 2"]').length).toBe(1);
		expect(document.body.querySelector('button[aria-label="Stop Brew 1"]')).toBeNull();
		await click(document.body.querySelector<HTMLElement>('button[aria-label="Stop Brew 2"]'));
		expect(cancelRuns).toHaveBeenCalledWith('e1', { run_ids: ['r2'] });
	});

	it('stops the selected running brews after a confirm and skips finished ones', async () => {
		listRuns.mockResolvedValue(page([run(3, 'running'), run(2, 'awaiting_selection'), run(1)], { active: 2 }));
		await open();
		for (const label of ['Select Brew 3', 'Select Brew 2', 'Select Brew 1']) {
			await click(document.body.querySelector<HTMLElement>(`input[aria-label="${label}"]`));
		}
		expect(document.body.querySelector('[data-testid="stop-note"]')?.textContent).toContain('2 can be stopped');
		await click(byText('Stop 2'));
		expect(cancelRuns).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('Stop 2 brews?');
		expect(document.body.textContent).toContain('1 selected brew is not running');
		await click(confirmButton('Stop 2 brews'));
		await vi.waitFor(() => expect(cancelRuns).toHaveBeenCalledWith('e1', { run_ids: ['r3', 'r2'] }));
	});

	it('disables Stop when only finished brews are selected', async () => {
		listRuns.mockResolvedValue(page([run(2, 'running'), run(1)], { active: 1 }));
		await open();
		await click(document.body.querySelector<HTMLElement>('input[aria-label="Select Brew 1"]'));
		expect(document.body.querySelector('[data-testid="stop-note"]')?.textContent).toContain('nothing to stop');
		expect((byText('Stop') as HTMLButtonElement).disabled).toBe(true);
	});

	it('stops every active brew after a confirm, even with filters', async () => {
		listRuns.mockResolvedValue(page([run(2, 'running'), run(1, 'running')], { active: 4 }));
		await open();
		await click(byText('Finished'));
		await click(byText('Failed'));
		await click(byText('Stop all running (4)'));
		expect(cancelRuns).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('Stop all 4 running brews?');
		expect(document.body.textContent).toContain('This includes brews your filters are hiding.');
		await click(confirmButton('Stop 4 brews'));
		await vi.waitFor(() => expect(cancelRuns).toHaveBeenCalledWith('e1', { all_active: true }));
	});

	it('shows a stopping row with a spinner instead of Stop', async () => {
		listRuns.mockResolvedValue(page([run(2, 'cancelling')], { active: 1 }));
		await open();
		expect(document.body.querySelector('button[aria-label="Stop Brew 2"]')).toBeNull();
		expect(document.body.querySelector('[aria-label="Stopping Brew 2"]')).toBeTruthy();
		expect(rows()[0].textContent).toContain('Stopping');
	});

	it('toasts when a stop is refused', async () => {
		listRuns.mockResolvedValue(page([run(2, 'running')], { active: 1 }));
		cancelRuns.mockResolvedValue({ ok: false, error: { code: 'error', message: 'Not allowed' } });
		await open();
		await click(document.body.querySelector<HTMLElement>('button[aria-label="Stop Brew 2"]'));
		expect(toastError).toHaveBeenCalledWith('Not allowed');
	});

	it('explains an elixir without brews', async () => {
		listRuns.mockResolvedValue(page([]));
		await open();
		expect(document.body.textContent).toContain('No brews yet');
		expect(rows().length).toBe(0);
	});

	it('explains a filter without matches and clears it', async () => {
		listRuns.mockResolvedValue(page([run(1)]));
		await open();
		listRuns.mockResolvedValue(page([]));
		await click(byText('Failed'));
		expect(document.body.textContent).toContain('No brews match');
		listRuns.mockResolvedValue(page([run(1)]));
		await click(byText('Clear filters'));
		expect(lastQuery().status).toBeUndefined();
		expect(rows().length).toBe(1);
	});

	it('shows the load error with Retry', async () => {
		listRuns.mockResolvedValue({ ok: false, error: { code: 'network', message: 'Earlier brews could not be loaded.' } });
		await open();
		expect(document.body.textContent).toContain('Earlier brews could not be loaded.');
		listRuns.mockResolvedValue(page([run(1)]));
		await click(byText('Retry') ?? byText('Try again'));
		expect(rows().length).toBe(1);
	});

	it('opens a brew from its row', async () => {
		listRuns.mockResolvedValue(page([run(2)]));
		await open();
		await click(document.body.querySelector<HTMLElement>('button[aria-label="Open Brew 2"]'));
		expect(onopen).toHaveBeenCalledWith(expect.objectContaining({ id: 'r2', number: 2 }));
	});
});
