// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getModelById: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		setOnAuthExpired: vi.fn(),
		getToken: vi.fn(() => null),
		getBaseURL: vi.fn(() => 'http://localhost')
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelField } = await import('$lib/components/form-fields/ModelField.svelte');

const MODEL = {
	id: 'MDL01K7',
	filename: 'base.safetensors',
	file_path: 'checkpoints/base.safetensors',
	model_type: 'checkpoint',
	name: 'Base model'
};

const flush = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

describe('ModelField clear control', () => {
	let instance: ReturnType<typeof mount> | null = null;
	let onChange: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [MODEL], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModelById).mockResolvedValue({ success: true, data: { model: MODEL } } as never);
		onChange = vi.fn();
	});

	afterEach(() => {
		if (instance) unmount(instance);
		instance = null;
		document.body.innerHTML = '';
	});

	async function render(required: boolean) {
		instance = mount(ModelField as never, {
			target: document.body,
			props: {
				name: 'model',
				config: { title: 'Model', required, configuration: { model_type: 'checkpoint' } },
				value: { modelPath: `model:${MODEL.id}`, tagFilters: [] },
				onChange
			}
		});
		await flush(30);
	}

	it('an optional field clears the picked model in one click', async () => {
		await render(false);
		const clear = document.querySelector<HTMLElement>('[aria-label="Clear model"]');
		expect(clear).not.toBeNull();
		clear!.click();
		await flush(10);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange.mock.calls[0][1]).toMatchObject({ modelPath: '' });
	});

	it('a required field offers no clear', async () => {
		await render(true);
		expect(document.body.textContent).toContain('Base model');
		expect(document.querySelector('[aria-label="Clear model"]')).toBeNull();
	});
});
