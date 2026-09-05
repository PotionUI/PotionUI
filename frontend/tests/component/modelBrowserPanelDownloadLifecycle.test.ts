// @vitest-environment jsdom
//
// ModelBrowserPanel's recommendation-download flow awaited api.startModelDownload
// with no disposed/owner check, then handed off to a bare `setInterval` poll
// that `onDestroy` only cleared if it already existed at teardown time - so a
// start response landing after unmount installed a poll that outlived the
// component, and a slow status response could overlap the next tick's fetch.
// This drives the real component with controllable deferred responses across
// unmount and reopen.
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

const RECOMMENDATION = { name: 'Suggested Model', provider: 'civitai', ref: 'ref-1' };
const EMPTY_MODELS = { success: true, data: { models: [], total: 0, availability_indexed: true } };

function mountPanel(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onSelect = vi.fn();
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: {
			modelType: 'checkpoint',
			onSelect,
			recommendations: [RECOMMENDATION],
			...props
		}
	});
	return { target, component, destroy: () => component.$destroy() };
}

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	vi.useRealTimers();
});

describe('ModelBrowserPanel recommendation-download lifecycle', () => {
	it('never starts a poll timer when the start response lands after the panel is destroyed', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
		vi.mocked(api.getModels).mockResolvedValue(EMPTY_MODELS as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		const startResponse = deferred<unknown>();
		vi.mocked(api.startModelDownload).mockReturnValue(startResponse.promise as never);

		mounted = mountPanel();
		await vi.advanceTimersByTimeAsync(0);

		const downloadButton = mounted.target.querySelector<HTMLButtonElement>(
			'button[title="Download this model"]'
		);
		expect(downloadButton, 'download button rendered for the unmatched recommendation').toBeTruthy();
		downloadButton!.click();
		await vi.advanceTimersByTimeAsync(0);

		// Destroy before the start call resolves.
		mounted.destroy();
		mounted = undefined;

		startResponse.resolve({ success: true, data: { download_id: 'dl-1' } });
		await vi.advanceTimersByTimeAsync(0);

		// If a poll got scheduled after teardown, this would fire a status
		// fetch; give it several poll periods' worth of fake time to prove
		// none is pending.
		await vi.advanceTimersByTimeAsync(10_000);
		expect(vi.mocked(api.getModelDownloadStatus)).not.toHaveBeenCalled();
	});

	it('reschedules the next status poll only after the previous one resolves, never overlapping', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
		vi.mocked(api.getModels).mockResolvedValue(EMPTY_MODELS as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.startModelDownload).mockResolvedValue({
			success: true,
			data: { download_id: 'dl-1' }
		} as never);

		const slowStatus = deferred<unknown>();
		vi.mocked(api.getModelDownloadStatus).mockReturnValue(slowStatus.promise as never);

		mounted = mountPanel();
		await vi.advanceTimersByTimeAsync(0);
		mounted.target
			.querySelector<HTMLButtonElement>('button[title="Download this model"]')!
			.click();
		await vi.advanceTimersByTimeAsync(0);

		// First poll fires at +2000ms and is now slow.
		await vi.advanceTimersByTimeAsync(2000);
		expect(vi.mocked(api.getModelDownloadStatus)).toHaveBeenCalledTimes(1);

		// Even though more than one poll period passes while it's pending, no
		// second call is issued until the first resolves.
		await vi.advanceTimersByTimeAsync(10_000);
		expect(vi.mocked(api.getModelDownloadStatus)).toHaveBeenCalledTimes(1);

		slowStatus.resolve({ success: true, data: { status: 'running', progress: 0.5, error: null } });
		await vi.advanceTimersByTimeAsync(0);
		vi.mocked(api.getModelDownloadStatus).mockResolvedValue({
			success: true,
			data: { status: 'completed', progress: 1, error: null }
		} as never);

		// Next poll is scheduled 2000ms after the first one resolved.
		await vi.advanceTimersByTimeAsync(2000);
		expect(vi.mocked(api.getModelDownloadStatus)).toHaveBeenCalledTimes(2);
	});

	it('stops polling and clears its timer when the panel is destroyed mid-poll', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
		vi.mocked(api.getModels).mockResolvedValue(EMPTY_MODELS as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.startModelDownload).mockResolvedValue({
			success: true,
			data: { download_id: 'dl-1' }
		} as never);
		vi.mocked(api.getModelDownloadStatus).mockResolvedValue({
			success: true,
			data: { status: 'running', progress: 0.2, error: null }
		} as never);

		mounted = mountPanel();
		await vi.advanceTimersByTimeAsync(0);
		mounted.target
			.querySelector<HTMLButtonElement>('button[title="Download this model"]')!
			.click();
		await vi.advanceTimersByTimeAsync(0);

		await vi.advanceTimersByTimeAsync(2000);
		expect(vi.mocked(api.getModelDownloadStatus)).toHaveBeenCalledTimes(1);

		mounted.destroy();
		mounted = undefined;

		await vi.advanceTimersByTimeAsync(20_000);
		expect(vi.mocked(api.getModelDownloadStatus)).toHaveBeenCalledTimes(1);
	});
});
