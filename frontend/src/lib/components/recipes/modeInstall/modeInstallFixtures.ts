import type {
	ModeConsentRequest,
	ModeConsentSlot,
	ModeConsentVariant
} from '$lib/services/api/recipeModes';

const GB = 1_000_000_000;

export function variant(id: string, overrides: Partial<ModeConsentVariant> = {}): ModeConsentVariant {
	return {
		id,
		label: id,
		precision: null,
		filename: `${id}.safetensors`,
		size_bytes: GB,
		installed: false,
		gated: false,
		license_url: null,
		recommended: true,
		best_fit: false,
		note: null,
		...overrides
	};
}

export function slot(id: string, overrides: Partial<ModeConsentSlot> = {}): ModeConsentSlot {
	const variants = overrides.variants ?? [variant(`${id}-v`)];
	return {
		id,
		label: id,
		model_type: 'lora',
		required: true,
		extra: false,
		manual: false,
		used_by: [],
		disk_id: 'd0',
		satisfied_by: null,
		recommended_variant_id: variants[0].id,
		best_fit_variant_id: null,
		reason: '',
		variants,
		...overrides
	};
}

export function modesRequest(overrides: Partial<ModeConsentRequest> = {}): ModeConsentRequest {
	const model = slot('model', {
		label: 'Main model',
		model_type: 'diffusion_model',
		used_by: ['video', 'cinemagraph', 'motion'],
		recommended_variant_id: 'int8',
		best_fit_variant_id: 'nvfp4',
		variants: [
			variant('int8', { label: 'int8', size_bytes: 20 * GB, installed: true, recommended: false }),
			variant('nvfp4', { label: 'nvfp4', size_bytes: 11 * GB, best_fit: true }),
			variant('bf16', { label: 'bf16', size_bytes: 40 * GB, recommended: false, note: 'Needs about 40 GB' })
		]
	});
	const lora = slot('effect_lora@cinemagraph', {
		label: 'Cinemagraph LoRA',
		used_by: ['cinemagraph'],
		variants: [variant('cine', { size_bytes: 2 * GB })]
	});
	const patch = slot('patch@motion', {
		label: 'Motion patch',
		used_by: ['motion'],
		variants: [variant('motion', { size_bytes: 3 * GB, gated: true, license_url: 'https://example.test/licence' })]
	});
	const preview = slot('preview_decoder', {
		label: 'Preview decoder',
		extra: true,
		used_by: ['video'],
		variants: [variant('decoder', { size_bytes: GB / 10 })]
	});
	return {
		kind: 'modes',
		preset_id: 'p1',
		preset_name: 'LTX-2.5',
		target: {
			backend_id: 'native',
			label: 'This computer',
			kind: 'local',
			free_bytes: 800 * GB,
			gpu_name: 'NVIDIA GeForce RTX 5090',
			vram_gb: 32,
			options: [
				{ backend_id: 'native', label: 'This computer', kind: 'local', free_bytes: 800 * GB, gpu_name: null, vram_gb: 32 },
				{ backend_id: 'pod', label: 'Worker pod', kind: 'remote', free_bytes: 3 * GB, gpu_name: null, vram_gb: 24 }
			]
		},
		gpu: { name: 'NVIDIA GeForce RTX 5090', vram_gb: 32 },
		modes: [
			{
				id: 'video',
				label: 'Video',
				short_description: 'Text or image to video',
				selected: true,
				locked: true,
				ready: true,
				slot_ids: ['model'],
				extra_slot_ids: ['preview_decoder'],
				any_of: [],
				missing_bytes: 0
			},
			{
				id: 'cinemagraph',
				label: 'Cinemagraph',
				short_description: 'Make a still image loop',
				selected: false,
				locked: false,
				ready: false,
				slot_ids: ['model', 'effect_lora@cinemagraph'],
				extra_slot_ids: [],
				any_of: [],
				missing_bytes: 2 * GB
			},
			{
				id: 'motion',
				label: 'Motion transfer',
				short_description: 'Move motion onto a picture',
				selected: false,
				locked: false,
				ready: false,
				slot_ids: ['model', 'patch@motion'],
				extra_slot_ids: [],
				any_of: [],
				missing_bytes: 3 * GB
			}
		],
		slots: [model, lora, patch, preview],
		disk: [{ id: 'd0', target: 'native', label: 'Models folder', free_bytes: 800 * GB, reserve_bytes: 2 * GB }],
		estimate: { bytes_per_sec: 31_000_000, basis: 'median of the last 5 completed downloads' },
		providers: [],
		warnings: [],
		...overrides
	};
}
