// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const goto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...a: unknown[]) => goto(...a) }));
vi.mock('$lib/plans/meApi', async (orig) => {
	const actual = await orig<typeof import('$lib/plans/meApi')>();
	return {
		...actual,
		getMyLimits: async () => ({
			rows: [
				{ kind: 'storage_bytes', label: 'Storage', used: 5 * 2 ** 30, limit: 20 * 2 ** 30, format: 'bytes', resets_at: null, state: 'ok', percent: null, enforced: true }
			],
			meta: { planName: 'Tier 1', source: 'default', groupName: null, exempt: false, timezone: 'UTC', contactLine: null }
		}),
		getMyStorageBreakdown: async () => [{ key: 'image', label: 'Images', files: 3, bytes: 2 ** 30 }]
	};
});

import PlanUsageCard from '$lib/plans/components/PlanUsageCard.svelte';
import { resetLimitsState } from '$lib/plans/store';

let cleanup: (() => void) | undefined;

beforeEach(() => {
	goto.mockReset();
	resetLimitsState();
	cleanup?.();
});

describe('Review largest generations', () => {
	it('opens History sorted largest first', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = mount(PlanUsageCard, { target });
		cleanup = () => {
			unmount(instance);
			target.remove();
		};
		for (let i = 0; i < 6; i += 1) {
			await Promise.resolve();
			flushSync();
		}

		const button = Array.from(target.querySelectorAll('button')).find((b) =>
			(b.textContent || '').includes('Review largest generations')
		);
		expect(button).toBeDefined();
		button!.click();

		expect(goto).toHaveBeenCalledWith('/history?sort=largest');
	});
});
