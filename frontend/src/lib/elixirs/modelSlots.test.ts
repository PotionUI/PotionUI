import { describe, expect, it, vi } from 'vitest';
import { completeModelSlots, type ModelSlotDeps } from './modelSlots';
import type { AutopickSlot } from '$lib/form/modelSlotAutopick';

const modelField = (name: string, title: string) => ({
	name,
	type: 'model',
	title,
	required: true,
	configuration: { model_type: name }
});

const schema = {
	properties: {
		root: {
			type: 'tab',
			children: [
				{ name: 'steps', type: 'number', default: 8 },
				modelField('diffusion_model', 'Diffusion Model'),
				modelField('text_encoder', 'Text Encoder'),
				modelField('vae', 'VAE')
			]
		}
	}
};

function deps(options: { candidates?: Record<string, Array<{ id: string }>>; installed?: Record<string, string> } = {}): ModelSlotDeps {
	const candidates = options.candidates ?? {};
	const installed = options.installed ?? {};
	return {
		loadSchema: vi.fn(async () => schema),
		search: vi.fn(async (slot: AutopickSlot) => {
			const models = candidates[slot.name] ?? [];
			return { models, total: models.length };
		}),
		findInstalled: vi.fn(async (_slot: AutopickSlot, value: string) => (installed[value] ? { id: installed[value] } : null))
	};
}

const run = (formData: Record<string, unknown>, d: ModelSlotDeps) =>
	completeModelSlots('krea2', 'edit', undefined, formData, d);

describe('completeModelSlots', () => {
	it('fills a required model slot the session left empty from its only installed model', async () => {
		const d = deps({ candidates: { text_encoder: [{ id: 'te-1' }] } });
		const result = await run({ diffusion_model: 'model:dm', vae: 'model:vae' }, d);

		expect(result).toEqual({
			ok: true,
			formData: { diffusion_model: 'model:dm', vae: 'model:vae', text_encoder: 'model:te-1' }
		});
	});

	it('turns a saved filename into the installed model it names', async () => {
		const d = deps({ installed: { 'qwen_image_vae.safetensors': 'vae-1' } });
		const result = await run(
			{ diffusion_model: 'model:dm', text_encoder: 'model:te', vae: 'qwen_image_vae.safetensors' },
			d
		);

		expect(result).toMatchObject({ ok: true, formData: { vae: 'model:vae-1' } });
	});

	it('treats a saved filename that is not installed as empty and picks the only installed model', async () => {
		const d = deps({ candidates: { diffusion_model: [{ id: 'dm-1' }] } });
		const result = await run(
			{ diffusion_model: 'krea2_turbo_fp8_scaled.safetensors', text_encoder: 'model:te', vae: 'model:vae' },
			d
		);

		expect(result).toMatchObject({ ok: true, formData: { diffusion_model: 'model:dm-1' } });
	});

	it('names every slot it cannot settle instead of leaving it for the backend to reject', async () => {
		const d = deps({ candidates: { diffusion_model: [{ id: 'a' }, { id: 'b' }] } });
		const result = await run({ vae: 'model:vae' }, d);

		expect(result).toEqual({
			ok: false,
			message: 'Choose Diffusion Model, Text Encoder in the session this step uses and save it.'
		});
	});

	it('does not search for slots that already hold a model reference', async () => {
		const d = deps();
		await run({ diffusion_model: 'model:dm', text_encoder: 'model:te', vae: 'model:vae' }, d);

		expect(d.search).not.toHaveBeenCalled();
		expect(d.findInstalled).not.toHaveBeenCalled();
	});

	it('leaves the form untouched when the schema cannot be loaded', async () => {
		const d = deps();
		d.loadSchema = vi.fn(async () => {
			throw new Error('offline');
		});
		const result = await run({ vae: '' }, d);

		expect(result).toEqual({ ok: true, formData: { vae: '' } });
	});
});
