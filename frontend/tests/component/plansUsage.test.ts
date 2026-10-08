// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get } from 'svelte/store';
import UsageRows from '$lib/plans/components/UsageRows.svelte';
import MenuUsage from '$lib/plans/components/MenuUsage.svelte';
import LimitNotice from '$lib/plans/components/LimitNotice.svelte';
import type { LimitRow } from '$lib/plans/meApi';

vi.mock('$lib/plans/meApi', async (orig) => {
	const actual = await orig<typeof import('$lib/plans/meApi')>();
	return { ...actual, getMyLimits: vi.fn(async () => ({ rows: [], meta: { planName: null, source: 'none', groupName: null, exempt: false, timezone: null, contactLine: null } })),
		getMyStorageBreakdown: vi.fn(async () => []) };
});

import { getMyLimits } from '$lib/plans/meApi';
import { postUpload } from '$lib/components/form-fields/mediaLoaderUpload';
import {
	generateGate,
	limits,
	reportLimitRefusal,
	resetLimitsState
} from '$lib/plans/store';

const NOW = Date.parse('2026-10-05T19:48:00Z');

const storage: LimitRow = {
	kind: 'storage_bytes',
	label: 'Storage',
	used: 18.6 * 2 ** 30,
	limit: 20 * 2 ** 30,
	format: 'bytes',
	resets_at: null,
	state: 'warn',
	percent: null,
	enforced: true
};
const daily: LimitRow = {
	kind: 'generations_per_day',
	label: 'Generations today',
	used: 37,
	limit: 100,
	format: 'count',
	resets_at: '2026-10-06T00:00:00Z',
	state: 'ok',
	percent: null,
	enforced: true
};
const cloud: LimitRow = {
	kind: 'cloud_spend_month',
	label: 'Cloud budget',
	used: 36,
	limit: 50,
	format: 'percent',
	resets_at: '2026-11-01T00:00:00Z',
	state: 'ok',
	percent: null,
	enforced: true
};

let cleanup: (() => void) | undefined;

function render(component: any, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(component, { target, props });
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

const META = { planName: null, source: 'none', groupName: null, exempt: false, timezone: null, contactLine: null };
const mockRows = (rows: LimitRow[]) => vi.mocked(getMyLimits).mockResolvedValue({ rows, meta: META });
const dailyFull: LimitRow = { ...daily, used: 100, state: 'full' };
const storageFull: LimitRow = { ...storage, used: 20 * 2 ** 30, state: 'full' };

beforeEach(() => {
	mockRows([dailyFull, storageFull]);
	vi.useFakeTimers();
	vi.setSystemTime(NOW);
	resetLimitsState();
});

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.querySelectorAll('[data-limit-notice]').forEach((n) => n.remove());
	resetLimitsState();
	vi.useRealTimers();
});

describe('usage rows', () => {
	it('renders one row per limit from the descriptor', () => {
		const target = render(UsageRows, { rows: [storage, daily, cloud], now: NOW });

		const rows = target.querySelectorAll('[data-limit-row]');
		expect(rows).toHaveLength(3);
		expect(rows[0].textContent).toContain('Storage');
		expect(rows[0].textContent).toContain('18.6 / 20 GB');
		expect(rows[0].textContent).toContain('1.4 GB left');
		expect(rows[1].textContent).toContain('37 / 100');
		expect(rows[1].textContent).toContain('resets in 4 h');
	});

	it('shows the cloud budget as a percent with no money', () => {
		const target = render(UsageRows, { rows: [cloud], now: NOW });

		const text = target.textContent ?? '';
		expect(text).toContain('72%');
		expect(text).toContain('resets Nov 1');
		expect(text).not.toMatch(/\$|\b36\b|\b50\b/);
	});

	it('uses the warning tone near full and danger when full', () => {
		const target = render(UsageRows, {
			rows: [storage, { ...daily, used: 100, state: 'full' }],
			now: NOW
		});

		const fills = target.querySelectorAll('[data-testid="limit-bar-fill"]');
		expect(fills[0].className).toContain('bg-warning');
		expect(fills[1].className).toContain('bg-danger');
	});

	it('says there are no limits when the list is empty', () => {
		const target = render(UsageRows, { rows: [], now: NOW });
		expect(target.textContent).toContain('No limits on your account');
	});
});

describe('menu usage bar', () => {
	it('shows the closest limit and how many others exist', () => {
		const target = render(MenuUsage, { rows: [daily, storage, cloud], onOpen: () => {} });

		expect(target.textContent).toContain('Storage');
		expect(target.textContent).toContain('18.6 / 20 GB');
		expect(target.textContent).toContain('+2 limits');
	});

	it('opens the usage section on click', () => {
		const onOpen = vi.fn();
		const target = render(MenuUsage, { rows: [storage], onOpen });

		target.querySelector<HTMLButtonElement>('[data-menu-usage]')!.click();

		expect(onOpen).toHaveBeenCalledOnce();
		expect(target.textContent).not.toContain('+0');
	});
});

describe('generate refusal', () => {
	const refusal = {
		error: 'limit_exceeded' as const,
		kind: 'generations_per_day',
		label: 'Generations today',
		format: 'count' as const,
		resets_at: '2026-10-06T00:00:00Z'
	};

	it('disables generating with the message and a live countdown', async () => {
		reportLimitRefusal(refusal);
		const unsubscribe = generateGate.subscribe(() => {});

		expect(get(generateGate)?.reason).toBe('Daily limit reached, resets in 4 h 12 min. Ask your admin for more.');

		await vi.advanceTimersByTimeAsync(60_000);
		expect(get(generateGate)?.reason).toBe('Daily limit reached, resets in 4 h 11 min. Ask your admin for more.');

		unsubscribe();
	});

	it('shows the notice with the countdown and no cleanup action for the daily limit', () => {
		reportLimitRefusal(refusal);
		const target = render(LimitNotice, {});
		flushSync();

		const notice = document.body.querySelector('[data-limit-notice]')!;
		expect(notice.textContent).toContain('Daily limit reached');
		expect(notice.textContent).toContain('4 h 12 min');
		expect(notice.textContent).toContain('See my plan');
		expect(notice.textContent).not.toContain('Free up space');
	});

	it('offers freeing space when storage is full', () => {
		reportLimitRefusal({ error: 'limit_exceeded', kind: 'storage_bytes', label: 'Storage', format: 'bytes' });
		const target = render(LimitNotice, {});
		flushSync();

		expect(document.body.textContent).toContain('Storage is full');
		expect(document.body.textContent).toContain('Free up space');
	});

	it('mounts on the body in the shared overlay layer with an opaque surface', () => {
		reportLimitRefusal(refusal);
		const target = render(LimitNotice, {});
		flushSync();

		const notice = document.body.querySelector('[data-limit-notice]') as HTMLElement;
		expect(notice.parentElement).toBe(document.body);
		expect(target.contains(notice)).toBe(false);
		expect(Number(notice.style.zIndex)).toBeGreaterThanOrEqual(1000);
		expect(notice.classList.contains('bg-surface-1')).toBe(true);
	});

	it('enables generating again when the countdown reaches zero', async () => {
		reportLimitRefusal(refusal);
		const unsubscribe = generateGate.subscribe(() => {});
		expect(get(generateGate)).not.toBeNull();
		mockRows([{ ...daily, used: 0 }]);

		await vi.advanceTimersByTimeAsync(4 * 3600 * 1000 + 12 * 60 * 1000 + 2000);

		expect(get(generateGate)).toBeNull();
		unsubscribe();
	});

	it('pre-checks a full limit before any click', () => {
		limits.set([{ ...daily, used: 100, state: 'full' }]);
		const unsubscribe = generateGate.subscribe(() => {});

		expect(get(generateGate)?.title).toBe('Daily limit reached');
		unsubscribe();
	});

	it('does not block on a warning', () => {
		limits.set([storage]);
		const unsubscribe = generateGate.subscribe(() => {});

		expect(get(generateGate)).toBeNull();
		unsubscribe();
	});
});

describe('upload refusal', () => {
	class FakeXhr {
		status = 403;
		responseText = JSON.stringify({
			detail: { error: 'limit_exceeded', kind: 'storage_bytes', label: 'Storage', format: 'bytes' }
		});
		withCredentials = false;
		upload: Record<string, unknown> = {};
		onload: (() => void) | null = null;
		onerror: (() => void) | null = null;
		open() {}
		setRequestHeader() {}
		send() {
			queueMicrotask(() => this.onload?.());
		}
	}

	it('rejects the media field upload with the plain storage message and raises the gate', async () => {
		vi.stubGlobal('XMLHttpRequest', FakeXhr);
		try {
			await expect(postUpload(new File(['x'], 'a.png'), null, () => {})).rejects.toThrow(
				'Storage is full. Free up space in History or Library, then try again.'
			);
			const unsubscribe = generateGate.subscribe(() => {});
			expect(get(generateGate)?.kind).toBe('storage_bytes');
			unsubscribe();
		} finally {
			vi.unstubAllGlobals();
		}
	});
});
