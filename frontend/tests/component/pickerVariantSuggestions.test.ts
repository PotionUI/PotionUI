// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { SetupConsentVariant } from '$lib/services/api/setup';
import type { RecipeSlotVariants } from '$lib/services/api/recipes';

const authState = vi.hoisted(() => ({ user: null as null | { account_type: string } }));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		getUnindexedModelsCount: vi.fn(),
		listPresets: vi.fn(),
		getPresetSlotVariants: vi.fn(),
		downloadSlotVariant: vi.fn()
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { invalidatePresets } = await import('$lib/stores/presetsCatalog');
const { default: ModelBrowserPanel } = await import('../../src/lib/components/form-fields/ModelBrowserPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function variant(id: string, precision: string, overrides: Partial<SetupConsentVariant> = {}): SetupConsentVariant {
	return {
		id,
		label: id,
		precision,
		filename: `${id}.safetensors`,
		size_bytes: 13_140_000_000,
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

function slot(variants: SetupConsentVariant[]): RecipeSlotVariants {
	return {
		id: 'krea2-diffusion-model',
		label: 'Krea-2 Turbo (DiT)',
		kind: 'diffusion_model',
		model_type: 'diffusion_model',
		required: true,
		variants,
		recommended_variant_id: 'fp8',
		reason: 'Fits your 24 GB',
		recipe_id: 'krea2-starter',
		recipe_name: 'Krea-2 Starter',
		suggested_variant_id: 'fp8',
		suggested_reason: 'Fits your 24 GB'
	};
}

function mountPanel(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ModelBrowserPanel as never,
		target,
		props: { modelType: 'diffusion_model', presetId: 'krea2', onSelect: vi.fn(), ...props }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function setup(slots: RecipeSlotVariants[]) {
	vi.mocked(api.getPresetModels).mockResolvedValue({ success: true, data: { models: [], total: 0 } } as never);
	vi.mocked(api.getUnindexedModelsCount).mockResolvedValue({ success: true, data: { by_type: {} } } as never);
	vi.mocked(api.getPresetSlotVariants).mockResolvedValue({ gpu: {} as never, slots });
}

let mounted: ReturnType<typeof mountPanel> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	invalidatePresets();
	authState.user = null;
});

describe('model picker Suggested variants', () => {
	it('shows the GPU pick with its reason, attribution and collapsed other variants', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('bf16', 'bf16'), variant('fp8', 'fp8'), variant('int8', 'int8', { installed: true })])]);
		mounted = mountPanel();
		await settle();

		expect(api.getPresetSlotVariants).toHaveBeenCalledWith('krea2', 'diffusion_model');
		const section = mounted.target.querySelector('[data-picker-variant-suggestions]') as HTMLElement;
		expect(section.textContent).toContain('Suggested');
		expect(section.querySelector('[data-slot-variant="fp8"]')).not.toBeNull();
		expect(section.querySelector('[data-slot-variant-reason]')?.textContent).toBe('Fits your 24 GB');
		expect(section.querySelector('[data-slot-variant="bf16"]')).toBeNull();
		expect(section.querySelector('[data-slot-variant="int8"]')).toBeNull();
		expect(section.querySelector('[data-variant-attribution]')?.textContent).toContain(
			'Free to download thanks to Hugging Face and Comfy-Org, who uploaded this model.'
		);

		(section.querySelector('[data-picker-other-variants-toggle]') as HTMLButtonElement).click();
		await settle();
		expect(section.querySelector('[data-slot-variant="bf16"]')).not.toBeNull();
		expect(section.querySelector('[data-slot-variant="int8"]')).toBeNull();
	});

	it('Download starts the recipe slot variant download', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8'), variant('bf16', 'bf16')])]);
		vi.mocked(api.downloadSlotVariant).mockResolvedValue({ download_id: 'dl-1', filename: 'fp8.safetensors' });
		mounted = mountPanel();
		await settle();

		const row = mounted.target.querySelector('[data-slot-variant="fp8"]') as HTMLElement;
		(row.querySelector('button') as HTMLButtonElement).click();
		await settle();

		expect(api.downloadSlotVariant).toHaveBeenCalledWith({
			recipe_id: 'krea2-starter',
			artifact_id: 'krea2-diffusion-model',
			variant_id: 'fp8'
		});
	});

	it('renders nothing when every variant of the slot is installed', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([slot([variant('fp8', 'fp8', { installed: true }), variant('bf16', 'bf16', { installed: true })])]);
		mounted = mountPanel();
		await settle();

		expect(mounted.target.querySelector('[data-picker-variant-suggestions]')).toBeNull();
	});

	it('renders nothing when the preset has no recipe slot for this type', async () => {
		authState.user = { account_type: 'ADMIN' };
		setup([]);
		mounted = mountPanel();
		await settle();

		expect(mounted.target.querySelector('[data-picker-variant-suggestions]')).toBeNull();
	});

	it('never asks for variants for a non-admin', async () => {
		authState.user = { account_type: 'USER' };
		setup([slot([variant('fp8', 'fp8')])]);
		mounted = mountPanel();
		await settle();

		expect(api.getPresetSlotVariants).not.toHaveBeenCalled();
		expect(mounted.target.querySelector('[data-picker-variant-suggestions]')).toBeNull();
	});
});
