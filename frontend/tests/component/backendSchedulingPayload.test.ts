// @vitest-environment jsdom
//
// The Backends admin tab's create modal and detail-pane edit form both embed
// BackendForm's Scheduling section (scheduling_policy/scheduling_max_consecutive_same_model),
// but the actual request body is built by BackendsTab's own buildBackendPayload -
// this proves the two are actually wired together: the fields the admin sets in
// BackendForm survive into what createBackend/updateBackend are called with, and
// an existing "fair" backend's configuration is not silently dropped by editing
// an unrelated field.
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
	testBackend: vi.fn()
}));

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		onComputeStatus: () => () => {}
	}
}));

const adminApi = await import('$lib/services/admin-api');
const { default: BackendsTab } = await import('../../src/routes/admin/components/BackendsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

const ENGINE: EngineDescriptor = {
	engine: 'native',
	driver: 'native.remote',
	label: 'Native (Remote)',
	singleton: false,
	creatable: true,
	fields: []
};

function backend(overrides: Partial<Backend> = {}): Backend {
	return {
		id: 'b1',
		name: 'Worker 1',
		engine: 'native',
		driver: 'native.remote',
		enabled: true,
		is_default: true,
		priority: 1,
		timeout_seconds: 300,
		scheduling_policy: 'fair',
		scheduling_max_consecutive_same_model: 5,
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

function fillInput(el: Element | null, value: string) {
	if (!el) throw new Error('input not found');
	(el as HTMLInputElement).value = value;
	el.dispatchEvent(new Event('input', { bubbles: true }));
}

function selectOption(el: Element | null, value: string) {
	if (!el) throw new Error('select not found');
	(el as HTMLSelectElement).value = value;
	el.dispatchEvent(new Event('change', { bubbles: true }));
}

function clickButtonWithText(target: HTMLElement, text: string) {
	const button = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === text);
	if (!button) throw new Error(`button "${text}" not found`);
	button.click();
}

function mountTab() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: BackendsTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountTab> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('Backends admin: scheduling fields reach the API payload', () => {
	it('creating a backend carries the chosen scheduling policy and allowance', async () => {
		vi.mocked(adminApi.getBackendEngines).mockResolvedValue(ok([ENGINE]));
		vi.mocked(adminApi.getBackends).mockResolvedValue(ok([]));
		vi.mocked(adminApi.getAllBackendsHealth).mockResolvedValue(ok([]));
		vi.mocked(adminApi.createBackend).mockResolvedValue(ok(backend()));

		mounted = mountTab();
		await settle();

		clickButtonWithText(mounted.target, 'Add Backend');
		await settle();

		// BaseModal renders through the `portal` action, onto document.body -
		// outside `mounted.target` - so the create form must be queried globally.
		fillInput(document.querySelector('#create-backend-name'), 'New Worker');
		selectOption(document.querySelector('#create-backend-scheduling-policy'), 'fair');
		await settle();
		fillInput(document.querySelector('#create-backend-scheduling-allowance'), '5');
		await settle();

		clickButtonWithText(document.body, 'Create Backend');
		await settle();

		expect(adminApi.createBackend).toHaveBeenCalledTimes(1);
		const payload = vi.mocked(adminApi.createBackend).mock.calls[0][0];
		expect(payload.scheduling_policy).toBe('fair');
		expect(payload.scheduling_max_consecutive_same_model).toBe(5);
	});

	it('editing an unrelated field preserves an existing fair configuration', async () => {
		const existing = backend({ id: 'b1', name: 'Worker 1', scheduling_policy: 'fair', scheduling_max_consecutive_same_model: 5 });
		vi.mocked(adminApi.getBackendEngines).mockResolvedValue(ok([ENGINE]));
		vi.mocked(adminApi.getBackends).mockResolvedValue(ok([existing]));
		vi.mocked(adminApi.getAllBackendsHealth).mockResolvedValue(ok([]));
		vi.mocked(adminApi.updateBackend).mockResolvedValue(ok(existing));

		mounted = mountTab();
		await settle();

		const row = Array.from(mounted.target.querySelectorAll('[role="option"]')).find((r) =>
			r.textContent?.includes('Worker 1')
		);
		expect(row).toBeTruthy();
		(row as HTMLElement).click();
		await settle();

		// Touch a field that has nothing to do with scheduling - this must not
		// disturb the fair configuration already loaded into the edit draft.
		fillInput(mounted.target.querySelector('#edit-backend-priority'), '7');
		await settle();

		clickButtonWithText(mounted.target, 'Save');
		await settle();

		expect(adminApi.updateBackend).toHaveBeenCalledTimes(1);
		const payload = vi.mocked(adminApi.updateBackend).mock.calls[0][1];
		expect(payload.priority).toBe(7);
		expect(payload.scheduling_policy).toBe('fair');
		expect(payload.scheduling_max_consecutive_same_model).toBe(5);
	});
});
