// @vitest-environment jsdom
//
// A refresh mid bring-up used to risk showing the provision form instead of
// the live timeline the row already carries - what has to hold is that a
// linked row always wins the render, regardless of whether the backend is
// `configured` yet (it isn't, mid bring-up). Also covers the collapsed
// "Bring-up log" disclosure a completed row keeps its history behind.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ProvisionedCompute } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	getComputeProviders: vi.fn(),
	getProviderFields: vi.fn(),
	getProvisionedComputeByBackend: vi.fn(),
	provisionCompute: vi.fn(),
	refreshProvisionedComputeStatus: vi.fn(),
	startProvisionedCompute: vi.fn(),
	stopProvisionedCompute: vi.fn(),
	terminateProvisionedCompute: vi.fn()
}));

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		isConnected: () => true,
		connectAsync: vi.fn().mockResolvedValue(undefined),
		onComputeStatus: () => () => {}
	}
}));

const adminApi = await import('$lib/services/admin-api');
const { default: BackendInfrastructureSection } = await import(
	'../../src/routes/admin/components/BackendInfrastructureSection.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function progressEntry(overrides: Partial<ProvisionedCompute['progress'][number]> = {}) {
	return {
		stage: 'preparing',
		message: 'Preparing worker',
		percent: null,
		at: '2026-09-02T10:00:00Z',
		...overrides
	};
}

function computeRow(overrides: Partial<ProvisionedCompute> = {}): ProvisionedCompute {
	return {
		id: 'pc-1',
		provider_id: 'runpod',
		handle: 'pod-123',
		profile_name: 'RTX 4090 worker',
		status: 'provisioning',
		backend_id: 'backend-1',
		resource_ref: 'pod-123',
		gpu_type_id: null,
		region: null,
		created_by: null,
		status_detail: null,
		status_checked_at: null,
		progress: [],
		created_at: null,
		updated_at: null,
		...overrides
	};
}

function mountSection(
	props: Partial<{
		backendId: string;
		backendDriver: string;
		configured: boolean;
		backendEnabled: boolean;
	}> = {}
) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: BackendInfrastructureSection as never,
		target,
		props: {
			backendId: 'backend-1',
			backendDriver: 'native.remote',
			configured: false,
			backendEnabled: true,
			onStopped: () => {},
			onProvisioned: () => {},
			onTerminated: () => {},
			onEnableBackend: () => {},
			...props
		}
	});
	return {
		target,
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes(text)),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountSection> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('BackendInfrastructureSection', () => {
	it('shows the live timeline for a provisioning row on an unconfigured backend, not the provision form', async () => {
		const row = computeRow({
			status: 'provisioning',
			progress: [
				progressEntry({ stage: 'preparing', message: 'Preparing worker' }),
				progressEntry({ stage: 'creating', message: 'Creating pod', at: '2026-09-02T10:00:05Z' }),
				progressEntry({ stage: 'starting', message: 'Booting worker', at: '2026-09-02T10:00:10Z' })
			]
		});
		vi.mocked(adminApi.getProvisionedComputeByBackend).mockResolvedValue({ success: true, data: row });

		mounted = mountSection({ configured: false });
		await settle();

		expect(mounted.target.textContent).toContain('Preparing worker');
		expect(mounted.target.textContent).toContain('Creating pod');
		expect(mounted.target.textContent).toContain('Booting worker');
		expect(mounted.target.querySelector('#provision-provider')).toBeFalsy();
		expect(mounted.button('Provision')).toBeFalsy();
	});

	it('keeps a running row\'s progress behind a collapsed "Bring-up log" disclosure', async () => {
		const row = computeRow({
			status: 'running',
			status_detail: null,
			progress: [
				progressEntry({ stage: 'preparing', message: 'Preparing worker' }),
				progressEntry({ stage: 'ready', message: 'Worker ready', at: '2026-09-02T10:00:20Z' })
			]
		});
		vi.mocked(adminApi.getProvisionedComputeByBackend).mockResolvedValue({ success: true, data: row });

		mounted = mountSection({ configured: true });
		await settle();

		const toggle = mounted.button('Bring-up log');
		expect(toggle).toBeTruthy();
		expect(toggle?.getAttribute('aria-expanded')).toBe('false');
		expect(mounted.target.textContent).not.toContain('Preparing worker');

		toggle?.click();
		await settle();

		expect(mounted.button('Bring-up log')?.getAttribute('aria-expanded')).toBe('true');
		expect(mounted.target.textContent).toContain('Preparing worker');
		expect(mounted.target.textContent).toContain('Worker ready');
	});
});
