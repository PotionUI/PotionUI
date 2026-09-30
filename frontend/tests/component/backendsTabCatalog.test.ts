// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { Backend, EngineDescriptor } from '$lib/services/admin-api';

vi.mock('$lib/services/admin-api', () => ({
	indexBackendModels: vi.fn(),
	getBackendStats: vi.fn(),
	getBackendEngines: vi.fn(),
	getBackends: vi.fn(),
	getAllBackendsHealth: vi.fn(),
	createBackend: vi.fn(),
	updateBackend: vi.fn(),
	deleteBackend: vi.fn(),
	setDefaultBackend: vi.fn(),
	testBackend: vi.fn(),
	getCloudCatalog: vi.fn(),
	refreshCloudCatalog: vi.fn(),
	setCloudCatalogEnabled: vi.fn()
}));
vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		onComputeStatus: () => () => {}
	}
}));

const adminApi = await import('$lib/services/admin-api');
const { default: BackendsTab } = await import('../../src/routes/admin/components/BackendsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

const ENGINES: EngineDescriptor[] = [
	{ engine: 'cloud', driver: 'cloud.fake', label: 'Fake Cloud', singleton: false, creatable: true, fields: [] },
	{ engine: 'comfyui', driver: 'comfyui', label: 'ComfyUI', singleton: false, creatable: true, fields: [] }
];

function backend(overrides: Partial<Backend> = {}): Backend {
	return {
		id: 'b1',
		name: 'Cloud One',
		engine: 'cloud',
		driver: 'cloud.fake',
		enabled: true,
		is_default: false,
		priority: 1,
		timeout_seconds: 300,
		scheduling_policy: 'fifo',
		scheduling_max_consecutive_same_model: 3,
		configured: true,
		...overrides
	};
}

function ok<T>(data: T) {
	return { success: true, data };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountTab() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: BackendsTab as never, target, props: {} });
	return {
		target,
		tabNames: () =>
			Array.from(target.querySelectorAll('nav[aria-label="Backend details"] button')).map((b) => b.textContent?.trim()),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function openBackend(target: HTMLElement, name: string) {
	const row = Array.from(target.querySelectorAll('[role="row"]')).find((r) => r.textContent?.includes(name));
	if (!row) throw new Error(`row "${name}" not found`);
	(row as HTMLElement).click();
	await settle();
}

let mounted: ReturnType<typeof mountTab> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

function setup() {
	vi.mocked(adminApi.getBackendEngines).mockResolvedValue(ok(ENGINES));
	vi.mocked(adminApi.getBackends).mockResolvedValue(
		ok([backend(), backend({ id: 'b2', name: 'Comfy Box', engine: 'comfyui', driver: 'comfyui' })])
	);
	vi.mocked(adminApi.getAllBackendsHealth).mockResolvedValue(ok([]));
	vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(
		ok({
			backend_id: 'b1',
			driver: 'cloud.fake',
			total: 0,
			limit: 50,
			offset: 0,
			provider: { key: 'fake', label: 'Fake Cloud', data_notice: '', supports_cancel: true },
			state: null,
			counts: { total: 0, enabled: 0, missing: 0 },
			items: []
		})
	);
}

describe('Backends admin: Catalog tab', () => {
	it('is offered on a cloud backend, between Overview and Stats', async () => {
		setup();
		mounted = mountTab();
		await settle();
		await openBackend(mounted.target, 'Cloud One');

		expect(mounted.tabNames()).toEqual(['Overview', 'Catalog', 'Stats']);
	});

	it('is not offered on any other engine', async () => {
		setup();
		mounted = mountTab();
		await settle();
		await openBackend(mounted.target, 'Comfy Box');

		expect(mounted.tabNames()).toEqual(['Overview', 'Stats']);
	});

	it('loads the backend catalog when opened', async () => {
		setup();
		mounted = mountTab();
		await settle();
		await openBackend(mounted.target, 'Cloud One');

		const tab = Array.from(mounted.target.querySelectorAll('nav[aria-label="Backend details"] button')).find(
			(b) => b.textContent?.includes('Catalog')
		) as HTMLElement;
		tab.click();
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenCalledWith('b1', { limit: 50, offset: 0 });
		expect(mounted.target.textContent).toContain('No models yet');
	});
});
