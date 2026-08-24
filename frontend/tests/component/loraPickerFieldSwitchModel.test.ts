// @vitest-environment jsdom
//
// The lora_picker card's per-row "switch model" affordance: it must open the
// same ModelBrowserPanel surface the add-flow (and ModelField's own picker)
// use, pre-filtered to `model_type: lora`, and on selection replace only that
// row's `model` - the row's `strength` and its position in the list must
// survive untouched.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getModelById: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		// Reached via the auth store, which ModelDetailsModal pulls in.
		setOnAuthExpired: vi.fn(),
		getToken: vi.fn(() => null),
		getBaseURL: vi.fn(() => 'http://localhost')
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: LoraPickerField } = await import(
	'../../src/lib/components/form-fields/LoraPickerField.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const LORA_A = {
	id: 'MDL-A',
	filename: 'style-a.safetensors',
	file_path: 'models/loras/style-a.safetensors',
	model_type: 'lora',
	name: 'Style A'
};

const LORA_B = {
	id: 'MDL-B',
	filename: 'style-b.safetensors',
	file_path: 'models/loras/style-b.safetensors',
	model_type: 'lora',
	name: 'Style B'
};

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

function mountField(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LoraPickerField as never,
		target,
		props: {
			name: 'loras',
			config: { title: 'LoRAs', configuration: { model_type: 'lora' } },
			value: [],
			onChange: vi.fn(),
			...props
		}
	});
	return {
		target,
		component,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountField> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('LoraPickerField row switch-model affordance', () => {
	it('opens the browse panel filtered to lora and replaces only that row\'s model, keeping strength and position', async () => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A, LORA_B], total: 2, availability_indexed: true }
		} as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: LORA_A }
		} as never);

		const onChange = vi.fn();
		mounted = mountField({
			value: [
				{ model: 'model:MDL-A', strength: 0.42 },
				{ model: 'model:absent', strength: 0.7 }
			],
			onChange
		});

		// Library fetch resolving the already-added rows' metadata.
		await flush(20);

		const switchButtons = Array.from(
			mounted.target.querySelectorAll<HTMLButtonElement>('button[aria-label="Switch LoRA model"]')
		);
		expect(switchButtons, 'one switch button per row').toHaveLength(2);

		switchButtons[0].click();
		await flush(20);

		// The switch panel's own fetch is the one filtered to model_type: lora,
		// matching what the add-flow's ModelBrowserPanel already requests.
		const loraFetches = vi
			.mocked(api.getModels)
			.mock.calls.filter(([params]) => (params as { model_type?: string })?.model_type === 'lora');
		expect(loraFetches.length, 'switch panel fetched with the lora filter').toBeGreaterThan(0);

		const rows = mounted.target.querySelectorAll<HTMLElement>('div[role="button"]');
		expect(rows.length, 'browse panel rendered result rows').toBeGreaterThan(0);
		const styleBRow = Array.from(rows).find((row) => row.textContent?.includes('Style B'));
		expect(styleBRow, 'Style B row rendered in the switch panel').toBeTruthy();

		styleBRow!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await flush(20);

		expect(onChange).toHaveBeenCalledWith('loras', [
			{ model: 'model:MDL-B', strength: 0.42 },
			{ model: 'model:absent', strength: 0.7 }
		]);
	});

	it('closes the switch panel without emitting a change when Escape is pressed', async () => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModelById).mockResolvedValue({ success: true, data: { model: LORA_A } } as never);

		const onChange = vi.fn();
		mounted = mountField({
			value: [{ model: 'model:MDL-A', strength: 1 }],
			onChange
		});
		await flush(20);

		mounted.target
			.querySelector<HTMLButtonElement>('button[aria-label="Switch LoRA model"]')!
			.click();
		await flush(20);

		expect(mounted.target.querySelector('input[placeholder="Search LoRAs..."]')).toBeTruthy();

		mounted.target
			.querySelector<HTMLInputElement>('input[placeholder="Search LoRAs..."]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await flush(20);

		expect(mounted.target.querySelector('input[placeholder="Search LoRAs..."]')).toBeNull();
		expect(onChange).not.toHaveBeenCalled();
	});
});
