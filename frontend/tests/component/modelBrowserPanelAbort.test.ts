// @vitest-environment jsdom
//
// ModelBrowserPanel.fetchModels used a fetchSeq counter to ignore a stale
// response, but never aborted the in-flight request itself - a rapid filter
// change or an unmount left the superseded request running to completion
// against the server. This drives the real component and inspects the
// AbortSignal each api.* call actually receives.
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

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { logger } = await import('$lib/utils/logger');
const axios = (await import('axios')).default;
const { default: ModelBrowserPanel } = await import(
	'../../src/lib/components/form-fields/ModelBrowserPanel.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

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

function canceledError() {
	// What axios actually throws when a request's AbortSignal fires.
	return new axios.CanceledError();
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

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('ModelBrowserPanel request abort', () => {
	it('aborts the previous in-flight request when a new fetch supersedes it', async () => {
		const signals: AbortSignal[] = [];
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModels).mockImplementation(((..._args: unknown[]) => {
			signals.push(_args[1] as AbortSignal);
			return new Promise(() => {});
		}) as never);

		mounted = mountPanel({ favoritesOnly: false });
		await flush(0);
		expect(signals).toHaveLength(1);
		expect(signals[0].aborted).toBe(false);

		// An immediate (non-debounced) filter change supersedes the mount fetch.
		mounted.component.$set({ favoritesOnly: true });
		await flush(0);

		expect(signals).toHaveLength(2);
		expect(signals[0].aborted, 'the superseded request must be aborted').toBe(true);
		expect(signals[1].aborted).toBe(false);
	});

	it('aborts the in-flight request when the panel unmounts', async () => {
		const signals: AbortSignal[] = [];
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModels).mockImplementation(((..._args: unknown[]) => {
			signals.push(_args[1] as AbortSignal);
			return new Promise(() => {});
		}) as never);

		mounted = mountPanel({});
		await flush(0);
		expect(signals[0].aborted).toBe(false);

		mounted.destroy();
		mounted = undefined;

		expect(signals[0].aborted).toBe(true);
	});

	it('swallows a canceled request silently, without logging an error or clearing loading for the current request', async () => {
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		let rejectFirst!: (e: unknown) => void;
		vi.mocked(api.getModels)
			.mockImplementationOnce(
				() => new Promise((_resolve, reject) => (rejectFirst = reject)) as never
			)
			.mockImplementationOnce(() => Promise.resolve(modelResponse('result')) as never);

		mounted = mountPanel({ favoritesOnly: false });
		await flush(0);
		mounted.component.$set({ favoritesOnly: true });
		await flush(0);

		rejectFirst(canceledError());
		await flush(0);

		expect(logger.error).not.toHaveBeenCalled();
	});
});
