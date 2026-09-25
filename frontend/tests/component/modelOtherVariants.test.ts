// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import type { SetupConsentVariant } from '$lib/services/api/setup';
import type { RecipeSlotVariants } from '$lib/services/api/recipes';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModelSlotVariants: vi.fn(),
		downloadSlotVariant: vi.fn(),
		getModelDownloadStatus: vi.fn()
	}
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelOtherVariants } = await import('../../src/lib/components/recipes/ModelOtherVariants.svelte');

function variant(id: string, overrides: Partial<SetupConsentVariant> = {}): SetupConsentVariant {
	return {
		id,
		label: id,
		precision: id,
		filename: `${id}.safetensors`,
		size_bytes: 7_670_000_000,
		installed: false,
		gated: false,
		license_url: null,
		uploader: 'Comfy-Org',
		source: 'huggingface',
		repo_id: 'Comfy-Org/Krea-2',
		source_url: 'https://huggingface.co/Comfy-Org/Krea-2',
		is_recipe_default: false,
		fast: true,
		recommended: false,
		note: null,
		...overrides
	};
}

function slot(): RecipeSlotVariants {
	return {
		id: 'krea2-diffusion-model',
		label: 'Krea-2 Turbo (DiT)',
		kind: 'diffusion_model',
		model_type: 'diffusion_model',
		required: true,
		variants: [variant('bf16'), variant('fp8', { installed: true }), variant('nvfp4')],
		recommended_variant_id: 'fp8',
		reason: 'Already installed',
		recipe_id: 'krea2-starter',
		recipe_name: 'Krea-2 Starter',
		suggested_variant_id: 'nvfp4',
		suggested_reason: 'Fits your 32 GB',
		current_variant_id: 'fp8'
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(modelId = 'model-1') {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(ModelOtherVariants, { target, props: { modelId } });
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	vi.clearAllMocks();
});

describe('ModelOtherVariants', () => {
	it('lists sibling variants with format, size, uploader, suggestion and attribution', async () => {
		vi.mocked(api.getModelSlotVariants).mockResolvedValue({ gpu: {} as never, slot: slot() });
		render();
		await settle();

		expect(api.getModelSlotVariants).toHaveBeenCalledWith('model-1');
		const section = target.querySelector('[data-model-other-variants]') as HTMLElement;
		expect(target.textContent).toContain('Other variants');
		expect(section.querySelector('[data-slot-variant="fp8"]')).toBeNull();
		const nvfp4 = section.querySelector('[data-slot-variant="nvfp4"]') as HTMLElement;
		expect(nvfp4.textContent).toContain('Suggested');
		expect(nvfp4.textContent).toContain('7.14 GB');
		expect(nvfp4.querySelector('[data-slot-variant-reason]')?.textContent).toBe('Fits your 32 GB');
		expect(nvfp4.querySelector('[data-slot-variant-uploader]')?.textContent).toContain('Comfy-Org');
		expect(section.querySelector('[data-variant-attribution]')?.textContent).toContain(
			'thanks to Hugging Face and Comfy-Org'
		);
	});

	it('Download queues the chosen sibling', async () => {
		vi.mocked(api.getModelSlotVariants).mockResolvedValue({ gpu: {} as never, slot: slot() });
		vi.mocked(api.downloadSlotVariant).mockResolvedValue({ download_id: 'dl-2', filename: 'bf16.safetensors' });
		render();
		await settle();

		const row = target.querySelector('[data-slot-variant="bf16"]') as HTMLElement;
		(row.querySelector('button') as HTMLButtonElement).click();
		await settle();

		expect(api.downloadSlotVariant).toHaveBeenCalledWith({
			recipe_id: 'krea2-starter',
			artifact_id: 'krea2-diffusion-model',
			variant_id: 'bf16'
		});
	});

	it('renders nothing for a model that is not part of a recipe slot', async () => {
		vi.mocked(api.getModelSlotVariants).mockResolvedValue({ gpu: {} as never, slot: null });
		render();
		await settle();

		expect(target.querySelector('[data-model-other-variants]')).toBeNull();
		expect(target.textContent).not.toContain('Other variants');
	});
});
