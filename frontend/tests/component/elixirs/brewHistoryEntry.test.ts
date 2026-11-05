import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const listElixirs = vi.fn();

vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => ({ listElixirs }) }));

import ElixirHeader from '$lib/components/elixirs/ElixirHeader.svelte';
import AllElixirsDialog from '$lib/components/elixirs/AllElixirsDialog.svelte';

let instance: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
	listElixirs.mockReset();
});

function brew(number: number, status = 'completed') {
	return { id: `r${number}`, number, status, scope: 'elixir', created_at: '2026-11-04T10:00:00Z', finished_at: null };
}

function header(props: Record<string, unknown>) {
	instance = mount(ElixirHeader as never, {
		target: document.body,
		props: {
			name: 'Portrait',
			runMode: 'review',
			runModes: [{ id: 'review', label: 'Choose outputs between presets' }],
			shortRunModes: [{ id: 'review', label: 'Choose outputs' }],
			saveState: 'saved',
			statusText: '',
			setupCodes: new Set<string>(),
			...props
		}
	});
	flushSync();
}

const brewsTrigger = () => document.body.querySelector<HTMLButtonElement>('button[aria-controls="elixir-brews"]')!;

describe('elixir header brews', () => {
	it('shows the server total and the pulsing active count on the trigger', () => {
		header({ runs: [brew(312, 'running'), brew(311)], runsTotal: 312, activeRuns: 4 });
		expect(brewsTrigger().getAttribute('aria-label')).toBe('Brews 312, 4 active');
		expect(brewsTrigger().textContent).toContain('312');
		expect(document.body.querySelector('[data-testid="brews-live"]')?.textContent).toContain('4');
	});

	it('shows no active marker when nothing runs', () => {
		header({ runs: [brew(2)], runsTotal: 2, activeRuns: 0 });
		expect(document.body.querySelector('[data-testid="brews-live"]')).toBeNull();
		expect(brewsTrigger().getAttribute('aria-label')).toBe('Brews 2');
	});

	it('lists only the latest 5 by server number and offers View all', () => {
		const onviewall = vi.fn();
		const runs = [312, 311, 310, 309, 308, 307, 306].map((number) => brew(number));
		header({ runs, runsTotal: 312, activeRuns: 0, onviewall });
		brewsTrigger().click();
		flushSync();
		const panel = document.body.querySelector('#elixir-brews')!;
		const names = [...panel.querySelectorAll('ul li')].map((item) => item.textContent?.trim());
		expect(names.length).toBe(5);
		expect(names[0]).toContain('Brew 312');
		expect(names[4]).toContain('Brew 308');
		const viewAll = panel.querySelector<HTMLButtonElement>('[data-testid="view-all-brews"]')!;
		expect(viewAll.textContent).toContain('View all 312 brews');
		viewAll.click();
		expect(onviewall).toHaveBeenCalledTimes(1);
	});

	it('names the footer Brew history when everything is already listed', () => {
		header({ runs: [brew(2), brew(1)], runsTotal: 2, onviewall: vi.fn() });
		brewsTrigger().click();
		flushSync();
		expect(document.body.querySelector('[data-testid="view-all-brews"]')?.textContent).toContain('Brew history');
	});
});

describe('All elixirs active brews badge', () => {
	it('badges only the elixir that has active brews', async () => {
		listElixirs.mockResolvedValue({
			ok: true,
			data: {
				elixirs: [
					{ id: 'a', name: 'Busy', status: 'running', active_runs: 3, node_count: 2 },
					{ id: 'b', name: 'Quiet', status: 'idle', active_runs: 0, node_count: 1 }
				]
			}
		});
		instance = mount(AllElixirsDialog as never, { target: document.body, props: { onclose: vi.fn() } });
		flushSync();
		await vi.waitFor(() => expect(document.body.querySelectorAll('[data-testid="active-brews-badge"]').length).toBe(1));
		const badge = document.body.querySelector('[data-testid="active-brews-badge"]')!;
		expect(badge.getAttribute('aria-label')).toBe('3 active brews');
		expect(badge.textContent).toContain('3');
	});
});
