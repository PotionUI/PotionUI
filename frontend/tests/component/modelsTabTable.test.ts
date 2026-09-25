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

vi.mock('$lib/services/api/index', () => ({
	api: {
		setOnAuthExpired: () => {},
		getToken: () => null,
		getModels: (...args: unknown[]) => mockGetModels(...args),
		getModelTypes: (...args: unknown[]) => mockGetModelTypes(...args),
		getTags: (...args: unknown[]) => mockGetTags(...args),
		getUnindexedModelsCount: (...args: unknown[]) => mockGetUnindexedModelsCount(...args),
		getAttributeDefinitions: (...args: unknown[]) => mockGetAttributeDefinitions(...args)
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getEnabledBackends: (...args: unknown[]) => mockGetEnabledBackends(...args),
	getModelAssignmentSummary: (...args: unknown[]) => mockGetModelAssignmentSummary(...args)
}));

vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		invalidate: async () => {},
		invalidateAll: async () => {},
		preloadData: async () => {},
		preloadCode: async () => {},
		afterNavigate: () => {},
		beforeNavigate: () => {},
		pushState: () => {},
		replaceState: () => {}
	};
});

const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: ModelsTab } = await import('../../src/routes/admin/components/ModelsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

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
	mockGetEnabledBackends.mockResolvedValue({ success: true, data: [] });
	mockGetModelAssignmentSummary.mockResolvedValue({ success: true, data: {} });
});

afterEach(() => {
	component?.$destroy();
	target?.remove();
	component = undefined;
});

describe('ModelsTab', () => {
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

	it('selecting a type section asks the API for that model_type', async () => {
		mountTab();
		await flush();
		flushSync();

		const sidebarRows = Array.from(target.querySelectorAll('[role="option"]'));
		const loraSection = sidebarRows.find((el) => el.textContent?.includes('LORA'))!;
		(loraSection as HTMLElement).click();
		await flush();
		flushSync();

		const lastCall = mockGetModels.mock.calls.at(-1)?.[0];
		expect(lastCall?.model_type).toBe('lora');
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
