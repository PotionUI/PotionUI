// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

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
const { default: LoraPickerField } = await import(
	'../../src/lib/components/form-fields/LoraPickerField.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');

registerBuiltinFieldComponents();

const LORA_A = {
	id: 'MDL-A',
	filename: 'style-a.safetensors',
	file_path: 'models/loras/style-a.safetensors',
	model_type: 'lora',
	name: 'Style A'
};

const AUDIO_ROW_FIELD = {
	name: 'audio',
	type: 'checkbox',
	title: 'Affects audio',
	default: true
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
			config: {
				title: 'LoRAs',
				configuration: { model_type: 'lora', row_fields: [AUDIO_ROW_FIELD] }
			},
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

describe('LoraPickerField row_fields configuration panel', () => {
	it('opens the configuration panel and shows the declared field checked to its default', async () => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getModelById).mockResolvedValue({ success: true, data: { model: LORA_A } } as never);

		mounted = mountField({ value: [{ model: 'model:MDL-A', strength: 1 }] });
		await flush(20);

		const gear = mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Configuration"]');
		expect(gear, 'a Configuration gear is rendered when row_fields is declared').toBeTruthy();

		expect(mounted.target.querySelector('input#audio')).toBeNull();

		gear!.click();
		await flush(20);

		const checkbox = mounted.target.querySelector<HTMLInputElement>('input#audio');
		expect(checkbox, 'the declared checkbox row field renders').toBeTruthy();
		expect(checkbox!.checked, 'absent audio defaults to true').toBe(true);
	});

	it('emits an updated row when the declared field is edited, and marks the gear when it differs from default', async () => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getModelById).mockResolvedValue({ success: true, data: { model: LORA_A } } as never);

		const onChange = vi.fn();
		mounted = mountField({ value: [{ model: 'model:MDL-A', strength: 1 }], onChange });
		await flush(20);

		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Configuration"]')!.click();
		await flush(20);

		expect(mounted.target.querySelector('.lora-row-config-marker'), 'no marker while unedited').toBeNull();

		mounted.target.querySelector<HTMLInputElement>('input#audio')!.click();
		await flush(20);

		expect(onChange).toHaveBeenCalledWith('loras', [{ model: 'model:MDL-A', strength: 1, audio: false }]);
	});

	it('does not render a Configuration gear when the field declares no row_fields', async () => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getModelById).mockResolvedValue({ success: true, data: { model: LORA_A } } as never);

		mounted = mountField({
			value: [{ model: 'model:MDL-A', strength: 1 }],
			config: { title: 'LoRAs', configuration: { model_type: 'lora' } }
		});
		await flush(20);

		expect(mounted.target.querySelector('button[aria-label="Configuration"]')).toBeNull();
	});
});
