// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { writable } from 'svelte/store';

const authState = writable<{ user: { account_type: string } | null }>({ user: null });
vi.mock('$lib/stores/auth', () => ({ authStore: authState }));

const getGenerationFailure = vi.fn();
vi.mock('$lib/services/admin-api', () => ({ getGenerationFailure }));

import type { AdminGenerationListItem, GenerationFailureDetail, RunReport } from '$lib/services/admin-api';

const { default: GenerationRunReport } = await import(
	'../../src/routes/admin/components/GenerationRunReport.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mount(generation: AdminGenerationListItem, report: RunReport | null = null) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationRunReport as never,
		target,
		props: { generation, report, username: 'alice' }
	});
	return {
		target,
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function baseGeneration(overrides: Partial<AdminGenerationListItem> = {}): AdminGenerationListItem {
	return {
		id: 'gen-1',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-08-14T00:00:00Z',
		completed_at: '2026-08-14T00:00:05Z',
		updated_at: '2026-08-14T00:00:05Z',
		files: [],
		rating: 0,
		is_favorite: false,
		user_id: 'user-1',
		has_run_report: false,
		...overrides
	};
}

function failureDetail(overrides: Partial<GenerationFailureDetail> = {}): GenerationFailureDetail {
	return {
		generation_id: 'gen-1',
		error_id: 'gen-1',
		error_code: 'cuda_oom',
		message: 'The GPU ran out of memory.',
		hint: '- Lower the resolution one tier',
		detail: 'Traceback (most recent call last):\n  torch.cuda.OutOfMemoryError',
		failed_pipe_id: 'generator',
		failed_pipe_name: 'SDXL generator',
		failed_at_step: 'Sampling',
		occurred_at: '2026-08-14T00:00:05Z',
		...overrides
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	authState.set({ user: null });
	getGenerationFailure.mockReset();
});

describe('GenerationRunReport failure section - admin gating', () => {
	it('fetches and renders category, failed pipe/step and a collapsed traceback with a copy control for an admin', async () => {
		authState.set({ user: { account_type: 'ADMIN' } });
		getGenerationFailure.mockResolvedValue({ success: true, data: failureDetail() });

		mounted = mount(baseGeneration({ status: 'failed', error_message: 'The GPU ran out of memory.' }));
		await settle();

		expect(getGenerationFailure).toHaveBeenCalledTimes(1);
		expect(getGenerationFailure).toHaveBeenCalledWith('gen-1');

		const text = mounted.text();
		expect(text).toContain('Failure');
		expect(text).toContain('GPU out of memory');
		expect(text).toContain('SDXL generator');
		expect(text).toContain('Sampling');

		const details = mounted.target.querySelector('details');
		expect(details).not.toBeNull();
		expect(details?.hasAttribute('open')).toBe(false);
		expect(details?.querySelector('pre')?.textContent).toContain('torch.cuda.OutOfMemoryError');
		expect(mounted.target.querySelector('button[aria-label="Copy traceback"]')).not.toBeNull();
	});

	it('never requests the failure detail for a non-admin viewer', async () => {
		authState.set({ user: { account_type: 'USER' } });

		mounted = mount(baseGeneration({ status: 'failed', error_message: 'The GPU ran out of memory.' }));
		await settle();

		expect(getGenerationFailure).not.toHaveBeenCalled();
		expect(mounted.text()).not.toContain('Traceback');
	});

	it('never requests the failure detail for a completed generation, even as an admin', async () => {
		authState.set({ user: { account_type: 'ADMIN' } });

		mounted = mount(baseGeneration({ status: 'completed' }));
		await settle();

		expect(getGenerationFailure).not.toHaveBeenCalled();
	});
});
