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

const MODELS = [
	model({ id: 'm-used', filename: 'flux-dev.safetensors', use_count: 42, last_used_at: new Date(Date.now() - 2 * 3600_000).toISOString() }),
	model({ id: 'm-unused', filename: 'sdxl-base.safetensors', use_count: 0, last_used_at: null })
];

function flush(times = 8) {
	let p: Promise<void> = Promise.resolve();
	for (let i = 0; i < times; i++) p = p.then(() => Promise.resolve());
	return p;
}

async function settle() {
	await flush();
	flushSync();
}

async function waitForDebounce() {
	await new Promise((resolve) => setTimeout(resolve, 300));
	await settle();
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountTab() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: ModelsTab as never, target, props: {} });
}

function searchInput(): HTMLInputElement {
	return target.querySelector('input[type="search"]') as HTMLInputElement;
}

function regexToggle(): HTMLButtonElement {
	return target.querySelector('button[aria-label="Regular expression"]') as HTMLButtonElement;
}

async function typeQuery(value: string) {
	const input = searchInput();
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	await waitForDebounce();
}

beforeEach(() => {
	vi.clearAllMocks();
	page.set({ url: new URL('http://localhost/admin?tab=models') });
	mockGetModels.mockResolvedValue({ success: true, data: { models: MODELS, total: MODELS.length, availability_indexed: false } });
	mockGetModelTypes.mockResolvedValue({ success: true, data: { types: [{ type: 'checkpoint', count: 2 }] } });
	mockGetTags.mockResolvedValue({ success: true, data: { tags: [] } });
	mockGetUnindexedModelsCount.mockResolvedValue({ success: true, data: { total: 0 } });
	mockGetAttributeDefinitions.mockResolvedValue({ success: true, data: { definitions: [] } });
	mockGetEnabledBackends.mockResolvedValue({ success: true, data: [] });
	mockGetModelAssignmentSummary.mockResolvedValue({ success: true, data: {} });
});

afterEach(() => {
	component?.$destroy();
	target?.remove();
	component = undefined;
});

describe('ModelsTab advanced search', () => {
	it('the regex toggle sends q_mode=regex with the query and shows its pressed state', async () => {
		mountTab();
		await settle();

		expect(regexToggle().getAttribute('aria-pressed')).toBe('false');
		regexToggle().click();
		await waitForDebounce();
		expect(regexToggle().getAttribute('aria-pressed')).toBe('true');

		await typeQuery('^flux-.*');

		const lastCall = mockGetModels.mock.calls.at(-1)?.[0];
		expect(lastCall).toMatchObject({ search: '^flux-.*', q_mode: 'regex' });
	});

	it('a plain search never sends q_mode', async () => {
		mountTab();
		await settle();

		await typeQuery('flux');

		const lastCall = mockGetModels.mock.calls.at(-1)?.[0];
		expect(lastCall?.search).toBe('flux');
		expect(lastCall).not.toHaveProperty('q_mode');
	});

	it('shows the server message inline when the regex is rejected with 422', async () => {
		mountTab();
		await settle();

		mockGetModels.mockRejectedValue({
			isAxiosError: true,
			message: 'Request failed with status code 422',
			response: {
				status: 422,
				data: { detail: { error: 'invalid_model_search', message: 'Invalid regular expression: missing ), unterminated subpattern at position 0' } }
			}
		});
		regexToggle().click();
		await waitForDebounce();
		await typeQuery('([');

		const alert = target.querySelector('[role="alert"]');
		expect(alert?.textContent).toContain('Invalid regular expression: missing ), unterminated subpattern');
		expect(target.textContent).not.toContain('flux-dev.safetensors');

		mockGetModels.mockResolvedValue({ success: true, data: { models: MODELS, total: MODELS.length, availability_indexed: false } });
		await typeQuery('flux');
		expect(target.querySelector('[role="alert"]')).toBeNull();
	});

	it('renders the Uses and Last used columns', async () => {
		mountTab();
		await settle();

		const header = target.textContent ?? '';
		expect(header).toContain('Uses');
		expect(header).toContain('Last used');

		const rows = Array.from(target.querySelectorAll('[role="row"]'));
		const used = rows.find((el) => el.textContent?.includes('flux-dev.safetensors'))!;
		const unused = rows.find((el) => el.textContent?.includes('sdxl-base.safetensors'))!;
		expect(used.textContent).toContain('42');
		expect(used.textContent).toContain('2h ago');
		expect(unused.textContent).toContain('Never');
	});

	it('restores advanced filters from the URL into chips and the API call', async () => {
		page.set({ url: new URL('http://localhost/admin?tab=models&used=never&indexed_from=2026-01-01&sort_by=last_used_desc') });
		mountTab();
		await settle();

		const lastCall = mockGetModels.mock.calls.at(-1)?.[0];
		expect(lastCall).toMatchObject({ used: 'never', indexed_from: '2026-01-01', sort_by: 'last_used', sort_order: 'desc' });
		expect(target.querySelector('button[aria-label="Remove filter Never used"]')).not.toBeNull();
		expect(target.querySelector('button[aria-label="Remove filter Indexed from 2026-01-01"]')).not.toBeNull();
	});
});
