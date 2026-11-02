import { describe, it, expect, vi, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		getUnindexedModelsCount: vi.fn(),
		listPresets: vi.fn(),
		getPresetSlotVariants: vi.fn()
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: {
		subscribe: (fn: (v: unknown) => void) => (fn({ user: { account_type: 'ADMIN' } }), () => {})
	}
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
	getErrorMessage: (error: unknown) => String(error)
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelBrowserPanel } = await import('../../src/lib/components/form-fields/ModelBrowserPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

const HF = 'https://huggingface.co/Lightricks/LTX-2.5';

const GATED = {
	name: 'ltx-2.5-transformer.safetensors',
	installed: false,
	provider: 'huggingface',
	ref: '{"repo": "Lightricks/LTX-2.5", "file": "diffusion_models/ltx.safetensors", "revision": "main"}',
	gated: true
};

const FREE = {
	name: 'free-model.safetensors',
	installed: false,
	provider: 'huggingface',
	ref: '{"repo": "org/free", "file": "free.safetensors"}'
};

const linksIn = (root: ParentNode) => Array.from(root.querySelectorAll<HTMLAnchorElement>('a[data-gated-link]'));

function rowFor(target: HTMLElement, name: string) {
	return Array.from(target.querySelectorAll('[data-recommended-download]')).find((el) =>
		el.textContent?.includes(name)
	) as HTMLElement;
}

let destroy: (() => void) | undefined;

function mountPanel(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: { onSelect: vi.fn(), presetId: 'pixal3d', ...props }
	});
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	return target;
}

function stubApi() {
	vi.mocked(api.getPresetModels).mockResolvedValue({ success: true, data: { models: [], total: 0 } } as never);
	vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
	vi.mocked(api.getPresetSlotVariants).mockResolvedValue({ gpu: {} as never, slots: [] });
	vi.mocked(api.startModelDownload).mockResolvedValue({ success: true, data: { download_id: 'd1' } } as never);
}

afterEach(() => {
	destroy?.();
	destroy = undefined;
	vi.clearAllMocks();
});

describe('ModelBrowserPanel gated recommendations', () => {
	it('shows the Hugging Face link on a gated recommendation and none on a free one', async () => {
		stubApi();
		const target = mountPanel({ modelType: 'diffusion_model', recommendations: [GATED, FREE] });
		await settle();

		const gated = rowFor(target, GATED.name);
		expect(gated.querySelector('[data-recommended-gated]')).not.toBeNull();
		expect(linksIn(gated)[0].getAttribute('href')).toBe(HF);
		expect(linksIn(rowFor(target, FREE.name))).toHaveLength(0);
	});

	it('shows the link next to the failure of a gated download', async () => {
		stubApi();
		vi.mocked(api.getModelDownloadStatus).mockResolvedValue({
			success: true,
			data: { status: 'failed', progress: null, error: 'Access denied (HTTP 403)', gated: true, gated_repo: 'org/free' }
		} as never);
		vi.useFakeTimers();
		try {
			const target = mountPanel({ modelType: 'diffusion_model', recommendations: [FREE] });
			await vi.advanceTimersByTimeAsync(0);
			(rowFor(target, FREE.name).querySelector('button') as HTMLButtonElement).click();
			await vi.advanceTimersByTimeAsync(2500);
			await vi.advanceTimersByTimeAsync(0);

			const row = rowFor(target, FREE.name);
			expect(row.textContent).toContain('Access denied (HTTP 403)');
			expect(linksIn(row)[0].getAttribute('href')).toBe('https://huggingface.co/org/free');
		} finally {
			vi.useRealTimers();
		}
	});

	it('shows no link when a free download fails', async () => {
		stubApi();
		vi.mocked(api.getModelDownloadStatus).mockResolvedValue({
			success: true,
			data: { status: 'failed', progress: null, error: 'HTTP 500' }
		} as never);
		vi.useFakeTimers();
		try {
			const target = mountPanel({ modelType: 'diffusion_model', recommendations: [{ ...FREE, provider: 'civitai', ref: '{"model_id": "1"}' }] });
			await vi.advanceTimersByTimeAsync(0);
			(rowFor(target, FREE.name).querySelector('button') as HTMLButtonElement).click();
			await vi.advanceTimersByTimeAsync(2500);
			await vi.advanceTimersByTimeAsync(0);

			expect(rowFor(target, FREE.name).textContent).toContain('HTTP 500');
			expect(linksIn(rowFor(target, FREE.name))).toHaveLength(0);
		} finally {
			vi.useRealTimers();
		}
	});
});
