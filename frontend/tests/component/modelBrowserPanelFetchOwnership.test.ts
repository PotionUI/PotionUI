// @vitest-environment jsdom
//
// ModelBrowserPanel.fetchModels used to read the response of whichever
// request resolved last, not whichever request was issued last. Two
// overlapping fetches (e.g. two rapid immediate filter changes, which are
// not debounced) resolving out of order would let a stale response clobber
// a newer one, and a stale response's `finally` could clear `loading` while
// the still-current request was in flight. This drives the real component
// (not a stubbed fetch) and resolves two in-flight requests out of order.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn()
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn({ user: null }), () => {}) }
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelBrowserPanel } = await import(
	'../../src/lib/components/form-fields/ModelBrowserPanel.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

function modelResponse(name: string) {
	return {
		success: true,
		data: {
			models: [{ id: name, filename: `${name}.safetensors`, model_type: 'checkpoint', name }],
			total: 1,
			availability_indexed: true
		}
	};
}

function mountPanel(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onSelect = vi.fn();
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: { modelType: 'checkpoint', onSelect, ...props }
	});
	return {
		target,
		component: component as { $set: (props: Record<string, unknown>) => void; $destroy: () => void },
		onSelect,
		destroy: () => component.$destroy()
	};
}

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

function rowNames(target: HTMLElement): string[] {
	return Array.from(target.querySelectorAll<HTMLElement>('div[role="button"]')).map(
		(row) => row.textContent?.trim() || ''
	);
}

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('ModelBrowserPanel fetch ownership', () => {
	it('keeps the result of the newest request even when an older request resolves later', async () => {
		const oldRequest = deferred<unknown>();
		const newRequest = deferred<unknown>();
		const calls: Array<ReturnType<typeof deferred<unknown>>> = [oldRequest, newRequest];
		vi.mocked(api.getModels).mockImplementation(() => calls.shift()!.promise as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);

		// Initial mount fetch (call #1, "old").
		mounted = mountPanel({ favoritesOnly: false });
		await flush(0);

		// An immediate (non-debounced) filter change fires call #2, "new",
		// while call #1 is still in flight.
		mounted.component.$set({ favoritesOnly: true });
		await flush(0);
		expect(vi.mocked(api.getModels)).toHaveBeenCalledTimes(2);

		// Resolve the newer request first, then the older one - the reversed
		// order the bug depended on.
		newRequest.resolve(modelResponse('new-result'));
		await flush(0);
		oldRequest.resolve(modelResponse('old-result'));
		await flush(0);

		expect(rowNames(mounted.target).some((text) => text.includes('new-result'))).toBe(true);
		expect(rowNames(mounted.target).some((text) => text.includes('old-result'))).toBe(false);
	});

	it('does not let a stale request clear loading out from under the current one', async () => {
		const oldRequest = deferred<unknown>();
		const newRequest = deferred<unknown>();
		const calls: Array<ReturnType<typeof deferred<unknown>>> = [oldRequest, newRequest];
		vi.mocked(api.getModels).mockImplementation(() => calls.shift()!.promise as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);

		mounted = mountPanel({ favoritesOnly: false });
		await flush(0);
		mounted.component.$set({ favoritesOnly: true });
		await flush(0);

		// Old request finishes first this time - it must not flip `loading`
		// off while the new (current) request is still pending.
		oldRequest.resolve(modelResponse('old-result'));
		await flush(0);
		expect(mounted.target.textContent).toContain('Loading models...');

		newRequest.resolve(modelResponse('new-result'));
		await flush(0);
		expect(mounted.target.textContent).not.toContain('Loading models...');
		expect(rowNames(mounted.target).some((text) => text.includes('new-result'))).toBe(true);
	});

	it('resolves tag ids against the scope active when the tag lookup was started, not a later one', async () => {
		// A tag-filter change kicks off resolveTagIds (an await on api.getTags)
		// before the search fetch is even issued. A scope change (search text)
		// landing while that tag lookup is still in flight must not mix the
		// newer text with a fetch that was superseded.
		const tagLookup = deferred<unknown>();
		vi.mocked(api.getTags).mockImplementation(() => tagLookup.promise as never);
		vi.mocked(api.getModels).mockResolvedValue(modelResponse('result') as never);

		mounted = mountPanel({ tagFilters: ['nsfw'] });
		await flush(0);
		expect(vi.mocked(api.getModels)).not.toHaveBeenCalled();

		// A new immediate trigger (favoritesOnly) supersedes the in-flight
		// mount fetch while its tag resolution is still pending.
		mounted.component.$set({ favoritesOnly: true });
		await flush(0);

		tagLookup.resolve({ success: true, data: { tags: [{ id: 'tag-1', name: 'nsfw' }] } });
		await flush(0);

		// The superseded fetch never reaches api.getModels at all - only the
		// newer request (favoritesOnly: true) does.
		expect(vi.mocked(api.getModels)).toHaveBeenCalledTimes(1);
		const params = vi.mocked(api.getModels).mock.calls[0][0] as Record<string, unknown>;
		expect(params.favorites_only).toBe(true);
	});
});
