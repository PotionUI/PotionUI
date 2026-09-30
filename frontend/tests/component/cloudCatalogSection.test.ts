// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get, type Writable } from 'svelte/store';
import type {
	CloudCatalogItem,
	CloudCatalogPage,
	CloudCatalogRefreshResult,
	CloudCatalogToggleResult
} from '$lib/services/admin-api';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/admin-api', () => ({
	getCloudCatalog: vi.fn(),
	refreshCloudCatalog: vi.fn(),
	setCloudCatalogEnabled: vi.fn()
}));
vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string | URL) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		}
	};
});

const adminApi = await import('$lib/services/admin-api');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: CloudCatalogSection } = await import('../../src/routes/admin/components/CloudCatalogSection.svelte');
const { createClassComponent } = await import('svelte/legacy');

function item(overrides: Partial<CloudCatalogItem> = {}): CloudCatalogItem {
	return {
		slug: 'model',
		provider_model_id: 'vendor/model',
		label: 'Model',
		vendor: 'vendor',
		description: null,
		tasks: ['txt2img'],
		outputs: ['image'],
		enabled: false,
		suggested: false,
		available: true,
		missing_since: null,
		deprecated: false,
		deprecated_at: null,
		discovered_at: null,
		refreshed_at: null,
		enabled_at: null,
		model_id: null,
		max_outputs_per_job: 1,
		typical_seconds: null,
		max_seconds: null,
		params: [],
		inputs: [],
		pricing: [],
		...overrides
	};
}

const VEO = item({
	slug: 'veo',
	provider_model_id: 'google/veo-3.1',
	label: 'Veo 3.1',
	vendor: 'google',
	tasks: ['txt2video', 'img2video'],
	outputs: ['video'],
	enabled: true,
	model_id: 'model-veo',
	deprecated: true,
	deprecated_at: '2026-10-01T00:00:00Z',
	pricing: [{ unit: 'second', usd: '0.40', applies_to: null }]
});
const SEEDREAM = item({
	slug: 'seedream',
	provider_model_id: 'bytedance/seedream-4.5',
	label: 'Seedream 4.5',
	vendor: 'bytedance',
	tasks: ['txt2img', 'img_edit'],
	suggested: true,
	pricing: [{ unit: 'image', usd: '0.04', applies_to: null }]
});
const FLUX = item({
	slug: 'flux',
	provider_model_id: 'bfl/flux-2',
	label: 'Flux 2',
	vendor: 'bfl',
	pricing: [{ unit: 'image', usd: '0.03', applies_to: null }]
});
const GONE = item({
	slug: 'gone',
	provider_model_id: 'old/gone',
	label: 'Gone Model',
	vendor: 'old',
	available: false,
	missing_since: '2026-09-20T00:00:00Z'
});

function catalog(items: CloudCatalogItem[], overrides: Partial<CloudCatalogPage> = {}): CloudCatalogPage {
	return {
		backend_id: 'b1',
		driver: 'cloud.fake',
		total: items.length,
		limit: 50,
		offset: 0,
		provider: {
			key: 'fake',
			label: 'Fake Cloud',
			data_notice: 'Prompts are sent to Fake Cloud and its upstream hosts.',
			supports_cancel: true
		},
		state: { refreshed_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(), listed: items.length, skipped: [] },
		counts: { total: items.length, enabled: items.filter((entry) => entry.enabled).length, missing: 0 },
		items,
		...overrides
	};
}

function ok<T>(data: T) {
	return { success: true, data };
}

function toggled(slug: string, enabled: boolean): CloudCatalogToggleResult {
	return {
		backend_id: 'b1',
		enabled,
		changed: [slug],
		unchanged: [],
		models: enabled ? { [slug]: `model-${slug}` } : {},
		index: null
	};
}

function refreshed(overrides: Partial<CloudCatalogRefreshResult> = {}): CloudCatalogRefreshResult {
	return {
		backend_id: 'b1',
		refreshed_at: '2026-09-30T10:00:00Z',
		listed: 4,
		accepted: 3,
		created: 2,
		vanished: ['old/gone'],
		skipped: [{ provider_model_id: 'odd/model', problems: ['no outputs'] }],
		index: null,
		empty: false,
		message: null,
		...overrides
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function mountSection() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: CloudCatalogSection as never, target, props: { backendId: 'b1' } });
	const gridRows = () =>
		Array.from(target.querySelectorAll<HTMLElement>('.dt-scroll > .dt-row:not(.dt-row--head)'));
	const rowFor = (label: string) => gridRows().find((row) => row.textContent?.includes(label));
	return {
		target,
		gridRows,
		rowFor,
		switchFor: (label: string) =>
			rowFor(label)?.querySelector<HTMLInputElement>('input[role="switch"]') ?? null,
		cardSwitchFor: (label: string) =>
			Array.from(target.querySelectorAll<HTMLElement>('.dt-mobile > div'))
				.find((card) => card.textContent?.includes(label))
				?.querySelector<HTMLInputElement>('input[role="switch"]') ?? null,
		checkboxFor: (label: string) =>
			rowFor(label)?.querySelector<HTMLButtonElement>('button[role="checkbox"]') ?? null,
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim().startsWith(text)),
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountSection> | undefined;

beforeEach(() => {
	page.set({ url: new URL('http://localhost/admin?tab=backends&backend=b1&view=catalog') });
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('Cloud catalog: list', () => {
	it('shows each model with its prices, tasks and status, plus the summary line and data notice', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM, GONE])));
		mounted = mountSection();
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenCalledWith('b1', { limit: 50, offset: 0 });
		expect(mounted.gridRows()).toHaveLength(3);

		const veo = mounted.rowFor('Veo 3.1')!.textContent!;
		expect(veo).toContain('google · google/veo-3.1');
		expect(veo).toContain('Text to video');
		expect(veo).toContain('Image to video');
		expect(veo).toMatch(/\$0\.40 \/ s(?![a-z])/);
		expect(veo).toContain('Deprecated');

		const seedream = mounted.rowFor('Seedream 4.5')!.textContent!;
		expect(seedream).toContain('$0.04 / image');
		expect(seedream).toContain('Text to image');
		expect(seedream).toContain('Edit');
		expect(seedream).toContain('Suggested');

		expect(mounted.rowFor('Gone Model')!.textContent).toContain('Missing from provider');
		expect(mounted.switchFor('Gone Model')!.disabled).toBe(true);
		expect(mounted.switchFor('Veo 3.1')!.checked).toBe(true);
		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);

		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toMatch(
			/Last refreshed 2h ago · 3 models · 1 enabled/
		);
		expect(mounted.target.querySelector('[data-testid="catalog-notice"]')!.textContent).toContain('upstream hosts');
	});

	it('passes the filters from the URL to the API', async () => {
		page.set({ url: new URL('http://localhost/admin?tab=backends&backend=b1&view=catalog&q=veo&task=txt2video&output=video&enabled=1') });
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted = mountSection();
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenCalledWith('b1', {
			limit: 50,
			offset: 0,
			task: 'txt2video',
			output: 'video',
			enabled: true,
			search: 'veo'
		});
	});

	it('offers a refresh when the catalog has never been loaded', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(
			ok(catalog([], { state: null, counts: { total: 0, enabled: 0, missing: 0 } }))
		);
		mounted = mountSection();
		await settle();

		expect(mounted.text()).toContain('No models yet');
		expect(mounted.text()).toContain("Refresh the catalog to load this provider's models.");
		expect(mounted.text()).toContain('Never refreshed');
		expect(mounted.gridRows()).toHaveLength(0);
	});

	it('says so when the filters match nothing', async () => {
		page.set({ url: new URL('http://localhost/admin?backend=b1&view=catalog&task=inpaint') });
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(
			ok(catalog([], { counts: { total: 3, enabled: 1, missing: 0 } }))
		);
		mounted = mountSection();
		await settle();

		expect(mounted.text()).toContain('No models match your filters');
	});

	it('shows a load error with a retry that loads again', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockRejectedValueOnce(new Error('network down'));
		mounted = mountSection();
		await settle();

		expect(mounted.text()).toContain('Could not load the catalog');
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted.button('Retry')!.click();
		await settle();

		expect(mounted.rowFor('Veo 3.1')).toBeTruthy();
		expect(mounted.text()).not.toContain('Could not load the catalog');
	});
});

describe('Cloud catalog: enable toggle', () => {
	it('turns the switch on straight away and keeps it on once the server agrees', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		const pending = deferred<ReturnType<typeof ok<CloudCatalogToggleResult>>>();
		vi.mocked(adminApi.setCloudCatalogEnabled).mockReturnValue(pending.promise);
		mounted = mountSection();
		await settle();

		mounted.switchFor('Seedream 4.5')!.click();
		await settle();

		expect(adminApi.setCloudCatalogEnabled).toHaveBeenCalledWith('b1', ['seedream'], true);
		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(true);
		expect(mounted.cardSwitchFor('Seedream 4.5')!.checked).toBe(true);

		pending.resolve(ok(toggled('seedream', true)));
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(true);
		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toContain('2 enabled');
	});

	it('puts the switch back and shows the error on the row when the server refuses', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		const pending = deferred<never>();
		vi.mocked(adminApi.setCloudCatalogEnabled).mockReturnValue(pending.promise);
		mounted = mountSection();
		await settle();

		mounted.switchFor('Seedream 4.5')!.click();
		await settle();
		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(true);
		expect(mounted.cardSwitchFor('Seedream 4.5')!.checked).toBe(true);

		pending.reject({ isAxiosError: true, message: 'Request failed', response: { data: { message: 'The provider is not reachable.' } } });
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.cardSwitchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.rowFor('Seedream 4.5')!.textContent).toContain('The provider is not reachable.');
		expect(mounted.rowFor('Veo 3.1')!.textContent).not.toContain('The provider is not reachable.');
		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toContain('1 enabled');
	});

	it('turns a model off the same way', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue(ok(toggled('veo', false)));
		mounted = mountSection();
		await settle();

		mounted.switchFor('Veo 3.1')!.click();
		await settle();

		expect(adminApi.setCloudCatalogEnabled).toHaveBeenCalledWith('b1', ['veo'], false);
		expect(mounted.switchFor('Veo 3.1')!.checked).toBe(false);
		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toContain('0 enabled');
	});
});

describe('Cloud catalog: refresh', () => {
	it('refreshes, summarises what changed and reloads the list', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		vi.mocked(adminApi.refreshCloudCatalog).mockResolvedValue(ok(refreshed()));
		mounted = mountSection();
		await settle();

		mounted.button('Refresh catalog')!.click();
		await settle();

		expect(adminApi.refreshCloudCatalog).toHaveBeenCalledWith('b1');
		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(2);
		expect(mounted.target.querySelector('[data-testid="refresh-summary"]')!.textContent).toMatch(
			/4 listed · 2 new · 1 missing from provider · 1 skipped/
		);
		expect(mounted.target.querySelector('[data-testid="skipped-list"]')).toBeNull();

		mounted.button('Show skipped models')!.click();
		await settle();

		const list = mounted.target.querySelector('[data-testid="skipped-list"]')!;
		expect(list.textContent).toContain('odd/model');
		expect(list.textContent).toContain('no outputs');
	});

	it('shows the message and leaves the list alone when the provider returned nothing', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		vi.mocked(adminApi.refreshCloudCatalog).mockResolvedValue(
			ok(
				refreshed({
					empty: true,
					message: 'The provider returned no models; nothing was changed.',
					listed: 0,
					accepted: 0,
					created: 0,
					vanished: [],
					skipped: [],
					refreshed_at: null
				})
			)
		);
		mounted = mountSection();
		await settle();

		mounted.button('Refresh catalog')!.click();
		await settle();

		expect(mounted.target.querySelector('[data-testid="refresh-empty"]')!.textContent).toContain(
			'nothing was changed'
		);
		expect(mounted.target.querySelector('[data-testid="refresh-summary"]')).toBeNull();
		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(1);
	});

	it('shows the provider failure inline', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		vi.mocked(adminApi.refreshCloudCatalog).mockRejectedValue({
			isAxiosError: true,
			message: 'Request failed',
			response: { data: { message: 'The provider rejected the API key.' } }
		});
		mounted = mountSection();
		await settle();

		mounted.button('Refresh catalog')!.click();
		await settle();

		expect(mounted.text()).toContain('The provider rejected the API key.');
	});
});

describe('Cloud catalog: bulk actions', () => {
	it('enables every selected model that is off in one request', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM, FLUX, GONE])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue(
			ok({ ...toggled('seedream', true), changed: ['seedream', 'flux'], models: { seedream: 'm-s', flux: 'm-f' } })
		);
		mounted = mountSection();
		await settle();

		for (const label of ['Veo 3.1', 'Seedream 4.5', 'Flux 2', 'Gone Model']) {
			mounted.checkboxFor(label)!.click();
			await settle();
		}
		expect(mounted.target.querySelector('[data-testid="bulk-bar"]')!.textContent).toContain('4 selected');

		mounted.button('Enable 2')!.click();
		await settle();

		expect(adminApi.setCloudCatalogEnabled).toHaveBeenCalledWith('b1', ['seedream', 'flux'], true);
		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(true);
		expect(mounted.switchFor('Flux 2')!.checked).toBe(true);
		expect(mounted.target.querySelector('[data-testid="bulk-bar"]')).toBeNull();
		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toContain('3 enabled');
	});

	it('disables the selected models that are on', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue(ok(toggled('veo', false)));
		mounted = mountSection();
		await settle();

		mounted.checkboxFor('Veo 3.1')!.click();
		await settle();
		mounted.button('Disable 1')!.click();
		await settle();

		expect(adminApi.setCloudCatalogEnabled).toHaveBeenCalledWith('b1', ['veo'], false);
		expect(mounted.switchFor('Veo 3.1')!.checked).toBe(false);
	});

	it('rolls every row back and explains when the bulk request fails', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([SEEDREAM, FLUX])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockRejectedValue({
			isAxiosError: true,
			message: 'Request failed',
			response: { data: { message: 'Cloud backend is not active.' } }
		});
		mounted = mountSection();
		await settle();

		mounted.checkboxFor('Seedream 4.5')!.click();
		await settle();
		mounted.checkboxFor('Flux 2')!.click();
		await settle();
		mounted.button('Enable 2')!.click();
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.switchFor('Flux 2')!.checked).toBe(false);
		expect(mounted.target.querySelector('[data-testid="bulk-bar"]')!.textContent).toContain(
			'Cloud backend is not active.'
		);
	});
});

function manyItems(count: number): CloudCatalogItem[] {
	return Array.from({ length: count }, (_, index) =>
		item({ slug: `m${index}`, provider_model_id: `v/m${index}`, label: `Model ${index}` })
	);
}

function pagedCatalog(count: number) {
	const all = manyItems(count);
	return async (_backendId: string, query: { limit?: number; offset?: number } = {}) => {
		const limit = query.limit ?? 50;
		const offset = query.offset ?? 0;
		return ok(catalog(all.slice(offset, offset + limit), { total: count, limit, offset }));
	};
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function urlParams() {
	return get(page).url.searchParams;
}

function type(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('Cloud catalog: search and filter writes', () => {
	const searchInput = () => document.querySelector<HTMLInputElement>('input[aria-label="Search the catalog"]')!;

	it('writes the typed search into the URL after the pause', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted = mountSection();
		await settle();

		type(searchInput(), 'veo');
		expect(urlParams().get('q')).toBeNull();
		await wait(400);

		expect(urlParams().get('q')).toBe('veo');
	});

	it('does not touch the URL when the tab is left before the pause ends', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted = mountSection();
		await settle();

		type(searchInput(), 'veo');
		mounted.destroy();
		mounted = undefined;
		await wait(400);

		expect(urlParams().get('q')).toBeNull();
	});

	it('merges the typed search with the filters as they are when the pause ends', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted = mountSection();
		await settle();

		type(searchInput(), 'veo');
		page.set({ url: new URL('http://localhost/admin?tab=backends&backend=b1&view=catalog&task=txt2video') });
		await wait(400);

		expect(urlParams().get('q')).toBe('veo');
		expect(urlParams().get('task')).toBe('txt2video');
	});

	it('drops a pending search when a filter is applied directly', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		mounted = mountSection();
		await settle();

		type(searchInput(), 'veo');
		mounted.button('Filters')!.click();
		await settle();
		const select = document.querySelector<HTMLSelectElement>('[role="dialog"][aria-label="Catalog filters"] select')!;
		select.value = 'txt2video';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await wait(400);

		expect(urlParams().get('task')).toBe('txt2video');
		expect(urlParams().get('q')).toBeNull();
	});
});

describe('Cloud catalog: reloading', () => {
	it('reloads with the new query from the first page when a filter changes', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		mounted = mountSection();
		await settle();
		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(1);

		page.set({ url: new URL('http://localhost/admin?backend=b1&view=catalog&task=txt2video&enabled=1') });
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(2);
		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', {
			limit: 50,
			offset: 0,
			task: 'txt2video',
			enabled: true
		});
	});

	it('goes back to the first page when a filter changes on a later page', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockImplementation(pagedCatalog(120));
		mounted = mountSection();
		await settle();
		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Next page"]')!.click();
		await settle();
		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', { limit: 50, offset: 50 });

		page.set({ url: new URL('http://localhost/admin?backend=b1&view=catalog&output=image') });
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', { limit: 50, offset: 0, output: 'image' });
	});

	it('shows the latest request when responses arrive out of order', async () => {
		const first = deferred<ReturnType<typeof ok<CloudCatalogPage>>>();
		vi.mocked(adminApi.getCloudCatalog)
			.mockReturnValueOnce(first.promise)
			.mockResolvedValue(ok(catalog([SEEDREAM])));
		mounted = mountSection();
		await settle();

		page.set({ url: new URL('http://localhost/admin?backend=b1&view=catalog&task=txt2img') });
		await settle();
		expect(mounted.rowFor('Seedream 4.5')).toBeTruthy();

		first.resolve(ok(catalog([VEO])));
		await settle();

		expect(mounted.rowFor('Seedream 4.5')).toBeTruthy();
		expect(mounted.rowFor('Veo 3.1')).toBeUndefined();
	});

	it('reloads after a toggle that raced a list reload instead of trusting the old rows', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		const pending = deferred<ReturnType<typeof ok<CloudCatalogToggleResult>>>();
		vi.mocked(adminApi.setCloudCatalogEnabled).mockReturnValue(pending.promise);
		mounted = mountSection();
		await settle();

		mounted.switchFor('Seedream 4.5')!.click();
		await settle();
		page.set({ url: new URL('http://localhost/admin?backend=b1&view=catalog&q=seed') });
		await settle();
		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(2);

		const enabledSeedream = item({ ...SEEDREAM, enabled: true, model_id: 'model-seedream' });
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, enabledSeedream])));
		pending.resolve(ok(toggled('seedream', true)));
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenCalledTimes(3);
		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(true);
		expect(mounted.target.querySelector('[data-testid="catalog-summary"]')!.textContent).toContain('2 enabled');
	});
});

describe('Cloud catalog: paging', () => {
	const nextButton = () => mounted!.target.querySelector<HTMLButtonElement>('button[aria-label="Next page"]')!;

	it('asks for the next fifty when going forward', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockImplementation(pagedCatalog(120));
		mounted = mountSection();
		await settle();
		expect(mounted.text()).toContain('Page 1 of 3');

		nextButton().click();
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', { limit: 50, offset: 50 });
		expect(mounted.text()).toContain('Page 2 of 3');
		expect(mounted.rowFor('Model 50')).toBeTruthy();
	});

	it('starts over from the first page when the page size changes', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockImplementation(pagedCatalog(120));
		mounted = mountSection();
		await settle();
		nextButton().click();
		await settle();

		const size = Array.from(mounted.target.querySelectorAll<HTMLSelectElement>('select')).find((el) =>
			Array.from(el.options).some((option) => option.value === '200')
		)!;
		size.value = '100';
		size.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', { limit: 100, offset: 0 });
		expect(mounted.text()).toContain('Page 1 of 2');
	});

	it('reloads from the first page after a refresh started on a later page', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockImplementation(pagedCatalog(120));
		vi.mocked(adminApi.refreshCloudCatalog).mockResolvedValue(ok(refreshed()));
		mounted = mountSection();
		await settle();
		nextButton().click();
		await settle();
		expect(mounted.text()).toContain('Page 2 of 3');

		mounted.button('Refresh catalog')!.click();
		await settle();

		expect(adminApi.getCloudCatalog).toHaveBeenLastCalledWith('b1', { limit: 50, offset: 0 });
		expect(mounted.text()).toContain('Page 1 of 3');
	});
});

describe('Cloud catalog: failures', () => {
	it('shows a refused refresh inline and clears it on Dismiss', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO])));
		vi.mocked(adminApi.refreshCloudCatalog).mockRejectedValue({
			isAxiosError: true,
			message: 'Request failed with status code 409',
			response: { status: 409, data: { message: 'Cloud backend is not active. Enable it first.' } }
		});
		mounted = mountSection();
		await settle();

		mounted.button('Refresh catalog')!.click();
		await settle();
		expect(mounted.text()).toContain('Cloud backend is not active. Enable it first.');

		mounted.button('Dismiss')!.click();
		await settle();
		expect(mounted.text()).not.toContain('Cloud backend is not active.');
	});

	it('rolls a row back and shows the message when the server answers unsuccessfully', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue({
			success: false,
			message: 'That model is no longer offered.'
		});
		mounted = mountSection();
		await settle();

		mounted.switchFor('Seedream 4.5')!.click();
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.rowFor('Seedream 4.5')!.textContent).toContain('That model is no longer offered.');
	});

	it('falls back to plain text when the unsuccessful answer has no message', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([VEO, SEEDREAM])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue({ success: false });
		mounted = mountSection();
		await settle();

		mounted.switchFor('Seedream 4.5')!.click();
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.rowFor('Seedream 4.5')!.textContent).toContain('Could not update this model.');
	});

	it('rolls a bulk change back when the server answers unsuccessfully', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue(ok(catalog([SEEDREAM, FLUX])));
		vi.mocked(adminApi.setCloudCatalogEnabled).mockResolvedValue({ success: false, message: 'Nothing was changed.' });
		mounted = mountSection();
		await settle();

		mounted.checkboxFor('Seedream 4.5')!.click();
		await settle();
		mounted.button('Enable 1')!.click();
		await settle();

		expect(mounted.switchFor('Seedream 4.5')!.checked).toBe(false);
		expect(mounted.target.querySelector('[data-testid="bulk-bar"]')!.textContent).toContain('Nothing was changed.');
	});

	it('shows the server message when loading answers unsuccessfully', async () => {
		vi.mocked(adminApi.getCloudCatalog).mockResolvedValue({ success: false, message: 'Backend not found.' });
		mounted = mountSection();
		await settle();

		expect(mounted.text()).toContain('Could not load the catalog');
		expect(mounted.text()).toContain('Backend not found.');
	});
});
