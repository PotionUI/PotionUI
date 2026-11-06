// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { EngineDescriptor } from '$lib/services/admin-api';
import { settle } from './helpers/settle';

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
		onComputeStatus: () => () => {},
		onIndexingStatus: () => () => {},
		onConnected: () => () => {},
		isConnected: () => true,
		connectAsync: async () => {}
	}
}));

const adminApi = await import('$lib/services/admin-api');
const { api } = await import('$lib/services/api');
vi.spyOn(api, 'getIndexingStatus').mockResolvedValue({ success: true, data: null });
const { default: BackendsTab } = await import('../../src/routes/admin/components/BackendsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

const ENGINES: EngineDescriptor[] = [
	{ engine: 'native', driver: 'native.remote', label: 'Native (Remote)', singleton: false, creatable: true, timeout_seconds: 300, fields: [] },
	{ engine: 'cloud', driver: 'cloud.fake', label: 'Fake Cloud', singleton: false, creatable: true, timeout_seconds: 1800, fields: [] }
];

function ok<T>(data: T) {
	return { success: true, data };
}

function selectOption(el: Element | null, value: string) {
	if (!el) throw new Error('select not found');
	(el as HTMLSelectElement).value = value;
	el.dispatchEvent(new Event('change', { bubbles: true }));
}

function clickButtonWithText(target: HTMLElement, text: string) {
	const button = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes(text));
	if (!button) throw new Error(`button "${text}" not found`);
	button.click();
}

function timeoutInput(): HTMLInputElement {
	return document.querySelector('#create-backend-timeout') as HTMLInputElement;
}

let destroy: (() => void) | undefined;

afterEach(() => {
	destroy?.();
	destroy = undefined;
	vi.clearAllMocks();
});

async function openCreateModal() {
	vi.mocked(adminApi.getBackendEngines).mockResolvedValue(ok(ENGINES));
	vi.mocked(adminApi.getBackends).mockResolvedValue(ok([]));
	vi.mocked(adminApi.getAllBackendsHealth).mockResolvedValue(ok([]));
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: BackendsTab as never, target, props: {} });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	await settle(10);
	clickButtonWithText(target, 'Add backend');
	await settle(10);
}

describe('Backends admin: the create form takes its time limit from the chosen driver', () => {
	it('a cloud driver starts at its own, longer limit', async () => {
		await openCreateModal();

		selectOption(document.querySelector('#create-backend-engine'), 'cloud.fake');
		await settle(10);

		expect(timeoutInput().value).toBe('1800');
	});

	it('the native driver keeps its own default and switching drivers follows the selection', async () => {
		await openCreateModal();

		selectOption(document.querySelector('#create-backend-engine'), 'cloud.fake');
		await settle(10);
		selectOption(document.querySelector('#create-backend-engine'), 'native.remote');
		await settle(10);

		expect(timeoutInput().value).toBe('300');
	});
});
