// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';

type PageStore = Writable<{ url: URL }>;

const mockGetModels = vi.fn();
const mockGetModelTypes = vi.fn();
const mockGetTags = vi.fn();
const mockGetUnindexedModelsCount = vi.fn();
const mockGetAttributeDefinitions = vi.fn();
const mockGetEnabledBackends = vi.fn();
const mockGetModelAssignmentSummary = vi.fn();
const mockClientGet = vi.fn();
const mockGetIndexingStatus = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		setOnAuthExpired: () => {},
		setOnTokenRefreshed: () => {},
		getToken: () => null,
		getModels: (...args: unknown[]) => mockGetModels(...args),
		getModelTypes: (...args: unknown[]) => mockGetModelTypes(...args),
		getTags: (...args: unknown[]) => mockGetTags(...args),
		getUnindexedModelsCount: (...args: unknown[]) => mockGetUnindexedModelsCount(...args),
		getAttributeDefinitions: (...args: unknown[]) => mockGetAttributeDefinitions(...args),
		getIndexingStatus: (...args: unknown[]) => mockGetIndexingStatus(...args),
		getClient: () => ({ get: (...args: unknown[]) => mockClientGet(...args) })
	}
}));

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		onIndexingStatus: () => () => {},
		onConnected: () => () => {},
		isConnected: () => true,
		connectAsync: async () => {}
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getEnabledBackends: (...args: unknown[]) => mockGetEnabledBackends(...args),
	getModelAssignmentSummary: (...args: unknown[]) => mockGetModelAssignmentSummary(...args)
}));

const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: ModelsTab } = await import('../../src/routes/admin/components/ModelsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { setModelTypes, resetModelTypes } = await import('$lib/stores/modelTypes.svelte');
const { coreModelTypeDefinitions } = await import('$lib/stores/modelTypesFixture');

function model(overrides: Record<string, unknown> = {}) {
	return {
		id: 'm1',
		filename: 'model.safetensors',
		model_type: 'checkpoint',
		file_size: 1024 * 1024 * 1024,
		tags: [],
		backend_ids: [],
		...overrides
	};
}

function definition(overrides: Record<string, unknown> = {}) {
	return {
		id: 'd1',
		key: 'strength',
		label: 'Strength',
		field_type: 'slider',
		model_types: [],
		config: {},
		default_value: null,
		per_user: false,
		admin_only: false,
		system: false,
		source: 'custom',
		...overrides
	};
}

const MODELS = [
	model({ id: 'm-checkpoint', filename: 'base.safetensors', model_type: 'checkpoint' }),
	model({ id: 'm-lora', filename: 'style.safetensors', model_type: 'lora' })
];

const DEFINITIONS = [
	definition({ id: 'custom-1', key: 'strength', label: 'Strength', system: false }),
	definition({ id: 'builtin-1', key: 'triggers', label: 'Trigger Words', system: true })
];

function flush(times = 8) {
	let p: Promise<void> = Promise.resolve();
	for (let i = 0; i < times; i++) p = p.then(() => Promise.resolve());
	return p;
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountTab() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: ModelsTab as never, target, props: {} });
}

beforeEach(() => {
	setModelTypes(coreModelTypeDefinitions());
	vi.clearAllMocks();
	page.set({ url: new URL('http://localhost/admin?tab=models') });
	mockGetModels.mockResolvedValue({ success: true, data: { models: MODELS, total: MODELS.length, availability_indexed: false } });
	mockGetModelTypes.mockResolvedValue({
		success: true,
		data: { types: [{ type: 'checkpoint', count: 1 }, { type: 'lora', count: 1 }] }
	});
	mockGetTags.mockResolvedValue({ success: true, data: { tags: [] } });
	mockGetUnindexedModelsCount.mockResolvedValue({ success: true, data: { total: 0 } });
	mockGetAttributeDefinitions.mockResolvedValue({ success: true, data: { definitions: DEFINITIONS } });
	mockGetIndexingStatus.mockResolvedValue({ success: true, data: null });
	mockGetEnabledBackends.mockResolvedValue({ success: true, data: [] });
	mockGetModelAssignmentSummary.mockResolvedValue({ success: true, data: {} });
	mockClientGet.mockImplementation((url: string) => {
		const data = url.endsWith('/catalog') ? { scope: 'instance', subjects: [], kinds: {}, operators: {}, facts: [], actions: [] } : [];
		return Promise.resolve({ data: { success: true, data } });
	});
});

afterEach(() => {
	resetModelTypes();
	component?.$destroy();
	target?.remove();
	component = undefined;
});

describe('ModelsTab', () => {
	describe('paging', () => {
		function pagedResponse() {
			return { success: true, data: { models: MODELS, total: 100, availability_indexed: false } };
		}

		function pageButton(label: string) {
			return target.querySelector<HTMLButtonElement>(`nav[aria-label="Pagination"] button[aria-label="${label}"]`)!;
		}

		it('stays on the chosen page and requests that offset', async () => {
			mockGetModels.mockResolvedValue(pagedResponse());
			mountTab();
			await flush();
			flushSync();
			expect(mockGetModels.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 30, offset: 0 });

			pageButton('Page 2').click();
			await flush();
			flushSync();
			await flush();

			expect(mockGetModels.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 30, offset: 30 });
			expect(pageButton('Page 2').getAttribute('aria-current')).toBe('page');
			const callsAfter = mockGetModels.mock.calls.length;
			await flush();
			expect(mockGetModels.mock.calls.length).toBe(callsAfter);
		});

		it('returns to the first page when the section changes', async () => {
			mockGetModels.mockResolvedValue(pagedResponse());
			mountTab();
			await flush();
			flushSync();
			pageButton('Page 2').click();
			await flush();
			flushSync();
			expect(pageButton('Page 2').getAttribute('aria-current')).toBe('page');

			const loraRow = Array.from(target.querySelectorAll('[data-pane-row]')).find((el) =>
				el.textContent?.trim().startsWith('LoRA')
			) as HTMLElement;
			loraRow.click();
			await flush();
			flushSync();
			await flush();

			expect(mockGetModels.mock.calls.at(-1)?.[0]).toMatchObject({ model_type: 'lora', offset: 0 });
			expect(pageButton('Page 1').getAttribute('aria-current')).toBe('page');
		});
	});

	it('renders models rows and the by-type/attributes sidebar counts', async () => {
		mountTab();
		await flush();
		flushSync();

		expect(target.textContent).toContain('base.safetensors');
		expect(target.textContent).toContain('style.safetensors');

		const sidebarRows = Array.from(target.querySelectorAll('[role="option"]'));
		const attributesRow = sidebarRows.find((el) => el.textContent?.includes('Attributes'));
		expect(attributesRow?.textContent).toContain(String(DEFINITIONS.length));
	});

	it('selecting a type row asks the API for that model_type', async () => {
		mountTab();
		await flush();
		flushSync();

		const typeRows = Array.from(target.querySelectorAll('[data-pane-row]'));
		const loraRow = typeRows.find((el) => el.textContent?.trim().startsWith('LoRA'))!;
		(loraRow as HTMLElement).click();
		await flush();
		flushSync();

		const lastCall = mockGetModels.mock.calls.at(-1)?.[0];
		expect(lastCall?.model_type).toBe('lora');
	});

	it('selecting a type row swaps the page title to that type while All models stays highlighted', async () => {
		mountTab();
		await flush();
		flushSync();

		expect(target.querySelector('h1')?.textContent).toContain('All models');

		const typeRows = Array.from(target.querySelectorAll('[data-pane-row]'));
		const loraRow = typeRows.find((el) => el.textContent?.trim().startsWith('LoRA'))!;
		(loraRow as HTMLElement).click();
		await flush();
		flushSync();

		expect(target.querySelector('h1')?.textContent).toContain('LoRA');
		expect(target.querySelector('h1')?.textContent).not.toContain('All models');

		const sectionRows = Array.from(target.querySelectorAll('[role="option"]'));
		const allModelsRow = sectionRows.find((el) => el.textContent?.includes('All models'))!;
		expect(allModelsRow.getAttribute('aria-selected')).toBe('true');
	});

	it('checking a model row surfaces the selection bar offering Assign access', async () => {
		mountTab();
		await flush();
		flushSync();

		const rows = Array.from(target.querySelectorAll('[role="row"]'));
		const row = rows.find((el) => el.textContent?.includes('base.safetensors'))!;
		const checkbox = row.querySelector('button[role="checkbox"]')!;
		(checkbox as HTMLElement).click();
		await flush();
		flushSync();

		const toolbar = target.querySelector('[role="toolbar"]')!;
		expect(toolbar.textContent).toContain('1 selected');
		expect(toolbar.textContent).toContain('Assign access');
	});

	it('switching to the Attributes section renders attribute rows instead of models', async () => {
		mountTab();
		await flush();
		flushSync();

		const sidebarRows = Array.from(target.querySelectorAll('[role="option"]'));
		const attributesRow = sidebarRows.find((el) => el.textContent?.includes('Attributes'))!;
		(attributesRow as HTMLElement).click();
		await flush();
		flushSync();

		expect(target.textContent).toContain('Strength');
		expect(target.textContent).toContain('Trigger Words');
		expect(target.textContent).not.toContain('base.safetensors');
	});

	it('lists Auto-organize as the last section and opens the instance rules without querying models', async () => {
		mountTab();
		await flush();
		flushSync();

		const sections = Array.from(target.querySelectorAll('[role="option"]')).map((el) => el.textContent ?? '');
		const names = ['All models', 'Attributes', 'Folders', 'Auto-organize'];
		const positions = names.map((name) => sections.findIndex((text) => text.includes(name)));
		expect(positions.every((index) => index >= 0)).toBe(true);
		expect([...positions].sort((a, b) => a - b)).toEqual(positions);

		mockGetModels.mockClear();
		const autoRow = Array.from(target.querySelectorAll('[role="option"]')).find((el) => el.textContent?.includes('Auto-organize'))!;
		(autoRow as HTMLElement).click();
		await flush();
		flushSync();
		page.set({ url: new URL('http://localhost/admin?tab=models&view=auto-organize') });
		await flush(20);
		flushSync();

		expect(target.querySelector('[data-testid="model-auto-organize"]')).not.toBeNull();
		expect(mockClientGet.mock.calls.map((c) => c[0])).toContain('/api/admin/organize/instance/rules');
		expect(mockGetModels.mock.calls.some((c) => c[0]?.model_type === 'auto-organize')).toBe(false);
		expect(target.textContent).not.toContain('base.safetensors');
	});

	it('a bulk selection covering a built-in attribute disables Delete', async () => {
		mountTab();
		await flush();
		flushSync();

		const sidebarRows = Array.from(target.querySelectorAll('[role="option"]'));
		const attributesRow = sidebarRows.find((el) => el.textContent?.includes('Attributes'))!;
		(attributesRow as HTMLElement).click();
		await flush();
		flushSync();

		const rows = Array.from(target.querySelectorAll('[role="row"]'));
		for (const row of rows) {
			const checkbox = row.querySelector('button[role="checkbox"]');
			(checkbox as HTMLElement)?.click();
		}
		await flush();
		flushSync();

		const toolbar = target.querySelector('[role="toolbar"]')!;
		const deleteButton = Array.from(toolbar.querySelectorAll('button')).find((b) => b.textContent?.includes('Delete'));
		expect(deleteButton?.hasAttribute('disabled')).toBe(true);
	});
});
