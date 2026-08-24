// @vitest-environment jsdom
//
// Clicking a LoRA trigger-word chip copies the trigger to the clipboard. It
// must NOT insert it into the active tab's prompt - with multiple prompt
// segments, inserting into the last/active segment is misleading about which
// segment actually receives the word.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';

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
const { tabsStore } = await import('$lib/stores/tabs');
const { toasts } = await import('$lib/stores/toast');

const LORA_A = {
	id: 'MDL-A',
	filename: 'style-a.safetensors',
	file_path: 'models/loras/style-a.safetensors',
	model_type: 'lora',
	name: 'Style A',
	model_metadata: { triggers: ['mytrigger'] }
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
	delete (navigator as { clipboard?: unknown }).clipboard;
});

describe('LoraPickerField trigger chip click', () => {
	it('copies the trigger word to the clipboard without touching the active prompt', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', {
			value: { writeText },
			configurable: true
		});

		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [LORA_A], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: LORA_A }
		} as never);

		// Default store state (no localStorage persisted tabs) has a single tab
		// with an empty prompt - exactly the setup where the old insert-on-click
		// behavior would silently append into that tab's prompt.
		const activeTabId = get(tabsStore).activeTabId;
		const promptBefore = get(tabsStore).tabs.find((t) => t.id === activeTabId)?.prompt;

		mounted = mountField({
			value: [{ model: 'model:MDL-A', strength: 1 }],
			onChange: vi.fn()
		});
		await flush(20);

		const chip = Array.from(mounted.target.querySelectorAll<HTMLButtonElement>('button')).find(
			(btn) => btn.textContent?.trim() === 'mytrigger'
		);
		expect(chip, 'trigger chip rendered').toBeTruthy();

		chip!.click();
		await flush(20);

		expect(writeText).toHaveBeenCalledWith('mytrigger');

		const promptAfter = get(tabsStore).tabs.find((t) => t.id === activeTabId)?.prompt;
		expect(promptAfter, 'active tab prompt left untouched').toBe(promptBefore);

		const lastToast = get(toasts).at(-1);
		expect(lastToast?.message).toBe('Copied to clipboard');
	});
});
