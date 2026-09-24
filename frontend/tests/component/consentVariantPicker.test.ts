import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type {
	SetupConsentGpuProfile,
	SetupConsentSlot,
	SetupConsentVariant
} from '$lib/services/api/setup';
import { formatBytes } from '$lib/utils/format';

const { default: ConsentVariantPicker } = await import(
	'../../src/lib/components/recipes/ConsentVariantPicker.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

const BEST_SIZE = 26_280_000_000;
const BALANCED_SIZE = 13_140_000_000;
const LOW_VRAM_SIZE = 7_670_000_000;
const VAE_SIZE = 250_000_000;

function variant(overrides: Partial<SetupConsentVariant> = {}): SetupConsentVariant {
	return {
		id: 'balanced',
		label: 'Balanced',
		precision: 'fp8',
		filename: 'krea2_balanced_fp8.safetensors',
		size_bytes: BALANCED_SIZE,
		installed: false,
		gated: false,
		license_url: null,
		uploader: 'Comfy-Org',
		source: 'huggingface',
		repo_id: 'Comfy-Org/Krea-2',
		source_url: 'https://huggingface.co/Comfy-Org/Krea-2',
		is_recipe_default: false,
		fast: true,
		recommended: true,
		note: null,
		...overrides
	};
}

function diffusionSlot(overrides: Partial<SetupConsentSlot> = {}): SetupConsentSlot {
	return {
		id: 'diffusion_model',
		label: 'Diffusion model',
		kind: 'checkpoint',
		model_type: 'diffusion_model',
		required: true,
		recommended_variant_id: 'balanced',
		reason: 'Fits your 24 GB',
		variants: [
			variant({
				id: 'best',
				label: 'Best quality',
				precision: 'bf16',
				size_bytes: BEST_SIZE,
				fast: false,
				recommended: false,
				note: 'Needs ~26 GB'
			}),
			variant(),
			variant({
				id: 'low_vram',
				label: 'Low VRAM',
				precision: 'nvfp4',
				size_bytes: LOW_VRAM_SIZE,
				fast: false,
				recommended: false,
				note: 'Not accelerated on your GPU'
			})
		],
		...overrides
	};
}

function vaeSlot(overrides: Partial<SetupConsentSlot> = {}): SetupConsentSlot {
	return {
		id: 'vae',
		label: 'VAE',
		kind: 'vae',
		model_type: 'vae',
		required: true,
		recommended_variant_id: 'vae',
		reason: 'Already installed',
		variants: [
			variant({
				id: 'vae',
				label: 'qwen_image_vae',
				precision: null,
				filename: 'qwen_image_vae.safetensors',
				size_bytes: VAE_SIZE,
				installed: true,
				uploader: null,
				repo_id: null,
				source_url: null
			})
		],
		...overrides
	};
}

function gpu(overrides: Partial<SetupConsentGpuProfile> = {}): SetupConsentGpuProfile {
	return {
		generation: 'ada',
		generation_label: 'RTX 40-series (Ada)',
		name: 'GeForce RTX 4090',
		vram_gb: 24,
		compute_capability: '8.9',
		fast_precisions: ['bf16', 'fp16', 'int8', 'fp8'],
		...overrides
	};
}

function mountPicker(props: {
	gpu: SetupConsentGpuProfile | null;
	slots: SetupConsentSlot[];
	selections: Record<string, string>;
	onPick: (slotId: string, variantId: string) => void;
}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(ConsentVariantPicker, { target, props });
	flushSync();
}

function unmountPicker() {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
}

function totalText(): string {
	const row = Array.from(target.querySelectorAll('div')).find((el) =>
		el.textContent?.trim().startsWith('Total to download')
	);
	return row?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function optionFor(variantId: string): HTMLElement | null {
	return target.querySelector(`[data-consent-variant="${variantId}"]`);
}

afterEach(() => {
	unmountPicker();
});

describe('ConsentVariantPicker', () => {
	it('defaults the checked option to the slot recommended_variant_id', () => {
		const slot = diffusionSlot();
		mountPicker({ gpu: gpu(), slots: [slot], selections: {}, onPick: vi.fn() });

		expect(optionFor('balanced')?.getAttribute('aria-checked')).toBe('true');
		expect(optionFor('best')?.getAttribute('aria-checked')).toBe('false');
		expect(optionFor('low_vram')?.getAttribute('aria-checked')).toBe('false');
	});

	it('reports the pick through onPick and the live total reflects the override', () => {
		const onPick = vi.fn();
		const slot = diffusionSlot();
		mountPicker({ gpu: gpu(), slots: [slot], selections: {}, onPick });

		expect(totalText()).toContain(formatBytes(BALANCED_SIZE));

		optionFor('best')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		expect(onPick).toHaveBeenCalledWith('diffusion_model', 'best');

		unmountPicker();
		mountPicker({ gpu: gpu(), slots: [slot], selections: { diffusion_model: 'best' }, onPick });
		expect(totalText()).toContain(formatBytes(BEST_SIZE));
		expect(optionFor('best')?.getAttribute('aria-checked')).toBe('true');
	});

	it('an installed variant contributes nothing to the total', () => {
		const slot = diffusionSlot();
		mountPicker({ gpu: gpu(), slots: [slot, vaeSlot()], selections: {}, onPick: vi.fn() });

		expect(totalText()).toContain(formatBytes(BALANCED_SIZE));
		expect(target.textContent).toContain('qwen_image_vae.safetensors');
		expect(target.textContent).toContain('Installed');
	});

	it('renders a gated warning with the licence link and one attribution per uploader', () => {
		const gatedSlot = diffusionSlot({
			id: 'transformer',
			label: 'Transformer',
			recommended_variant_id: 'gated_variant',
			variants: [
				variant({
					id: 'gated_variant',
					label: 'Standard',
					precision: 'int8',
					gated: true,
					license_url: 'https://huggingface.co/Lightricks/LTX-2.5',
					uploader: 'Lightricks',
					repo_id: 'Lightricks/LTX-2.5',
					source_url: 'https://huggingface.co/Lightricks/LTX-2.5'
				}),
				variant({
					id: 'alt',
					label: 'Alt',
					uploader: 'Lightricks',
					repo_id: 'Lightricks/LTX-2.5',
					source_url: 'https://huggingface.co/Lightricks/LTX-2.5'
				})
			]
		});
		mountPicker({ gpu: gpu(), slots: [gatedSlot], selections: {}, onPick: vi.fn() });

		expect(target.textContent).toContain('Licence required');
		const link = target.querySelector(
			'a[href="https://huggingface.co/Lightricks/LTX-2.5"][target="_blank"]'
		);
		expect(link).not.toBeNull();

		const attributionMatches = Array.from(target.querySelectorAll('span')).filter((el) =>
			el.textContent?.includes('Free to download thanks to Hugging Face and Lightricks')
		);
		expect(attributionMatches.length).toBe(1);
	});
});
