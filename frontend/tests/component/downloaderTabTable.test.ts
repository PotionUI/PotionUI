// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';

type PageStore = Writable<{ url: URL }>;

const mockGet = vi.fn();
const mockGetProviders = vi.fn();
const mockGetBackends = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: (...args: unknown[]) => mockGet(...args) }),
		getProviders: (...args: unknown[]) => mockGetProviders(...args),
		getToken: () => null
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getBackends: (...args: unknown[]) => mockGetBackends(...args)
}));

vi.mock('$lib/services/downloaderWebsocket', () => ({
	downloaderConnectionState: { subscribe: (run: (state: string) => void) => (run('connected'), () => {}) },
	downloaderWebSocket: {
		connectAsync: async () => {},
		disconnect: () => {},
		onDownloadProgress: () => () => {},
		onDownloadStatus: () => () => {},
		subscribeToAllDownloads: () => {},
		subscribeToDownload: () => {}
	}
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
const { downloadStore } = await import('$lib/stores/downloads');
const { default: DownloaderTab } = await import('../../src/routes/admin/components/DownloaderTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function download(overrides: Record<string, unknown> = {}) {
	return {
		id: 'd1',
		type: 'model',
		url: 'https://example.com/model.safetensors',
		destination_path: '/models/checkpoint/x.safetensors',
		filename: 'x.safetensors',
		status: 'downloading',
		progress: 0.5,
		total_bytes: 100,
		downloaded_bytes: 50,
		speed_bytes_per_sec: 10,
		error_message: null,
		provider_id: null,
		tags: [],
		checksum_sha256: null,
		retry_count: 0,
		created_at: '2026-01-01T00:00:00Z',
		started_at: null,
		completed_at: null,
		created_by: null,
		...overrides
	};
}

const ROWS = [
	download({ id: 'active-1', filename: 'active.safetensors', status: 'downloading' }),
	download({ id: 'fail-1', filename: 'broken.safetensors', status: 'failed', error_message: 'boom', retry_count: 2 })
];

function flush(times = 6) {
	let p: Promise<void> = Promise.resolve();
	for (let i = 0; i < times; i++) p = p.then(() => Promise.resolve());
	return p;
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountTab() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: DownloaderTab as never, target, props: {} });
}

beforeEach(() => {
	vi.clearAllMocks();
	downloadStore.reset();
	page.set({ url: new URL('http://localhost/admin?tab=downloads') });
	mockGet.mockResolvedValue({ data: { success: true, data: { downloads: ROWS, counts: {} } } });
	mockGetProviders.mockResolvedValue({ success: true, data: [] });
	mockGetBackends.mockResolvedValue({ success: true, data: [] });
});

afterEach(() => {
	component?.$destroy();
	target?.remove();
	component = undefined;
});

describe('DownloaderTab', () => {
	it('renders every row in the "all" section and the sidebar section counts', async () => {
		mountTab();
		await flush();
		flushSync();

		expect(target.textContent).toContain('active.safetensors');
		expect(target.textContent).toContain('broken.safetensors');

		const failedRow = Array.from(target.querySelectorAll('[role="option"]')).find((el) =>
			el.textContent?.includes('Failed')
		)!;
		expect(failedRow.textContent).toContain('1');
	});

	it('selecting the Failed sidebar section filters the table down to failed rows', async () => {
		mountTab();
		await flush();
		flushSync();

		const failedSection = Array.from(target.querySelectorAll('[role="option"]')).find((el) =>
			el.textContent?.includes('Failed')
		)!;
		(failedSection as HTMLElement).click();
		await flush();
		flushSync();

		expect(target.textContent).toContain('broken.safetensors');
		expect(target.textContent).not.toContain('active.safetensors');
	});

	it('checking a failed row surfaces the selection bar offering Retry and Remove but not Cancel', async () => {
		mountTab();
		await flush();
		flushSync();

		const rows = Array.from(target.querySelectorAll('[role="row"]'));
		const failedDataRow = rows.find((el) => el.textContent?.includes('broken.safetensors'))!;
		const rowCheckbox = failedDataRow.querySelector('button[role="checkbox"]')!;
		(rowCheckbox as HTMLElement).click();
		await flush();
		flushSync();

		const toolbar = target.querySelector('[role="toolbar"]')!;
		expect(toolbar.textContent).toContain('1 selected');
		expect(toolbar.textContent).toContain('Retry');
		expect(toolbar.textContent).toContain('Remove');
		expect(toolbar.textContent).not.toContain('Cancel');
	});

	it('clicking a row opens DownloadDetail with the filename as the header title', async () => {
		mountTab();
		await flush();
		flushSync();

		const rows = Array.from(target.querySelectorAll('[role="row"]'));
		const failedDataRow = rows.find((el) => el.textContent?.includes('broken.safetensors'))!;
		(failedDataRow as HTMLElement).click();
		await flush();
		flushSync();

		expect(target.querySelector('h2')?.textContent).toBe('broken.safetensors');
		expect(target.textContent).toContain('Downloads');
		expect(target.textContent).toContain('Retry');
		expect(target.textContent).not.toContain('Pause');
		expect(target.textContent).not.toContain('Resume');
	});
});
