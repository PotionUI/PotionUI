import { afterEach, describe, expect, it, vi } from 'vitest';
import { createFormStateSeed, type FormSeedDeps, type FormSeedTarget } from './formStateSeed';
import { forgetClearedSlots, markSlotCleared, type AutopickSlot } from './modelSlotAutopick';

const modelField = (name: string, title: string) => ({
	name,
	type: 'model',
	title,
	required: true,
	configuration: { model_type: name }
});

function schemaFor(stepsDefault = 30) {
	return {
		properties: {
			root: {
				type: 'tabs',
				children: [
					{
						type: 'tab',
						label: 'Generation',
						children: [
							{
								name: 'speed_profile',
								type: 'select',
								default: 'turbo',
								configuration: { options: [{ value: 'turbo' }, { value: 'base' }] }
							},
							{
								name: 'steps',
								type: 'number',
								default: stepsDefault,
								reactions: [{ when: { field: 'speed_profile', equals: 'turbo' }, then: { set_value: 8 } }]
							},
							modelField('diffusion_model', 'Diffusion Model'),
							modelField('text_encoder', 'Text Encoder'),
							modelField('vae', 'VAE')
						]
					}
				]
			}
		}
	};
}

const SINGLE: Record<string, Array<{ id: string }>> = {
	diffusion_model: [{ id: 'dm-1' }],
	text_encoder: [{ id: 'te-1' }],
	vae: [{ id: 'vae-1' }]
};

function harness(
	options: {
		rows?: Record<string, Array<{ id: string }>>;
		schemas?: Record<string, unknown>;
		deps?: Partial<FormSeedDeps>;
	} = {}
) {
	const rows = options.rows ?? SINGLE;
	const published: Record<string, unknown>[] = [];
	const missing: Array<{ key: string; name: string | null }> = [];
	const searched: string[] = [];
	const deps: FormSeedDeps = {
		loadSchema: vi.fn(async (_presetId: string, mode: string) => options.schemas?.[mode] ?? schemaFor()),
		search: vi.fn(async (slot: AutopickSlot) => {
			searched.push(slot.name);
			const models = rows[slot.name] ?? [];
			return { models, total: models.length };
		}),
		loadCapabilities: vi.fn(async () => undefined),
		capabilityOf: () => undefined,
		publish: (data) => {
			published.push(data);
		},
		publishMissing: (_tabId, key, entry) => {
			missing.push({ key, name: entry?.name ?? null });
		},
		clearMissing: vi.fn(),
		...options.deps
	};
	return { deps, published, missing, searched, seed: createFormStateSeed(deps) };
}

function target(overrides: Partial<FormSeedTarget> = {}): FormSeedTarget {
	return {
		tabId: 'tab-1',
		presetId: 'zimage',
		mode: 'txt2img',
		variant: null,
		formData: {},
		formPublished: false,
		audience: 'simple',
		videoDirectorActive: false,
		...overrides
	};
}

async function settled() {
	for (let i = 0; i < 10; i++) {
		if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0);
		else await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function deferred<T>() {
	let resolve: (value: T) => void = () => {};
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
}

const CLOUD_CAPS = {
	model_id: 'cloud-1',
	params: [{ name: 'steps', kind: 'range' as const, minimum: 1, maximum: 30, integer: true }],
	inputs: []
};

function cloudSchema() {
	return {
		properties: {
			root: {
				type: 'row',
				children: [
					modelField('diffusion_model', 'Model'),
					{ name: 'steps', type: 'number', default: 50, capability: { model_field: 'diffusion_model', param: 'steps' } },
					{ name: 'guidance', type: 'number', default: 3, capability: { model_field: 'diffusion_model', param: 'guidance' } }
				]
			}
		}
	};
}

afterEach(() => {
	vi.useRealTimers();
	forgetClearedSlots();
});

describe('form state seed without a mounted form', () => {
	it('fills the schema defaults, runs reactions and picks the single installed model of every required slot', async () => {
		const { seed, published, missing } = harness();
		seed.sync(target());
		await settled();

		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({
			speed_profile: 'turbo',
			steps: 8,
			diffusion_model: 'model:dm-1',
			text_encoder: 'model:te-1',
			vae: 'model:vae-1'
		});
		expect(missing.at(-1)).toEqual({ key: 'zimage-txt2img-', name: null });
	});

	it('keeps a saved selection and only fills what is missing', async () => {
		const { seed, published, searched } = harness();
		seed.sync(target({ formData: { diffusion_model: 'model:saved', steps: 20 } }));
		await settled();

		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({
			diffusion_model: 'model:saved',
			steps: 20,
			text_encoder: 'model:te-1',
			vae: 'model:vae-1'
		});
		expect(searched).not.toContain('diffusion_model');
	});

	it('leaves a slot the session saved empty and reports it missing', async () => {
		const { seed, published, missing, searched } = harness();
		seed.sync(target({ formData: { vae: '' } }));
		await settled();

		expect(published[0]).toMatchObject({ vae: '', diffusion_model: 'model:dm-1' });
		expect(searched).not.toContain('vae');
		expect(missing.at(-1)).toEqual({ key: 'zimage-txt2img-', name: 'vae' });
	});

	it('leaves a slot the user cleared empty', async () => {
		const { seed, published, searched } = harness();
		markSlotCleared('tab-1', 'text_encoder');
		seed.sync(target());
		await settled();

		expect(published[0]).toMatchObject({ text_encoder: '' });
		expect(searched).not.toContain('text_encoder');
	});

	it('picks nothing when a slot has two candidates', async () => {
		const { seed, published, missing } = harness({
			rows: { ...SINGLE, diffusion_model: [{ id: 'dm-1' }, { id: 'dm-2' }] }
		});
		seed.sync(target());
		await settled();

		expect(published[0]).toMatchObject({ diffusion_model: '', vae: 'model:vae-1' });
		expect(missing.at(-1)).toEqual({ key: 'zimage-txt2img-', name: 'diffusion_model' });
	});

	it('keeps chosen fields across a mode round trip', async () => {
		const { seed, published, searched } = harness({ schemas: { video: schemaFor(40) } });
		seed.sync(target({ formData: { diffusion_model: 'model:user-choice' } }));
		await settled();
		const imageForm = published[0];
		expect(imageForm).toMatchObject({ diffusion_model: 'model:user-choice', vae: 'model:vae-1' });

		seed.sync(target({ mode: 'video', formData: {}, formPublished: false }));
		await settled();
		expect(published[1]).toMatchObject({ steps: 8, diffusion_model: 'model:dm-1' });
		const searchesBeforeReturn = searched.length;

		seed.sync(target({ formData: imageForm, formPublished: false }));
		await settled();
		expect(published[2]).toEqual(imageForm);
		expect(searched).toHaveLength(searchesBeforeReturn);
	});

	it('does not reload, search or publish again for its own publication', async () => {
		const { seed, published, deps } = harness();
		seed.sync(target());
		await settled();
		seed.sync(target({ formData: published[0], formPublished: true }));
		seed.sync(target({ formData: published[0], formPublished: true }));
		await settled();

		expect(published).toHaveLength(1);
		expect(deps.loadSchema).toHaveBeenCalledTimes(1);
		expect(deps.search).toHaveBeenCalledTimes(3);
	});

	it('writes nothing when a published form is already complete', async () => {
		const first = harness();
		first.seed.sync(target());
		await settled();

		const { seed, published, deps } = harness();
		seed.sync(target({ formData: first.published[0], formPublished: true }));
		await settled();
		expect(published).toEqual([]);
		expect(deps.search).not.toHaveBeenCalled();
	});

	it('drops the result for a preset the tab already left', async () => {
		let releaseFirst: (schema: unknown) => void = () => {};
		const { seed, published, deps } = harness();
		vi.mocked(deps.loadSchema)
			.mockImplementationOnce(() => new Promise((resolve) => (releaseFirst = resolve)))
			.mockImplementationOnce(async () => schemaFor(12));
		seed.sync(target({ presetId: 'old' }));
		seed.sync(target({ presetId: 'new' }));
		await settled();
		releaseFirst(schemaFor());
		await settled();

		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({ diffusion_model: 'model:dm-1' });
	});

	it('stops and forgets its missing-model report once disposed', async () => {
		const { seed, published, deps } = harness();
		seed.sync(target());
		await settled();
		published.length = 0;
		seed.sync(target({ formData: { vae: 'model:other' }, formPublished: true }));
		seed.dispose();
		await settled();

		expect(published).toEqual([]);
		expect(deps.clearMissing).toHaveBeenCalledWith('tab-1');
	});

	it('does nothing until the tab has both a preset and a mode', async () => {
		const { seed, published, deps } = harness();
		seed.sync(target({ mode: null }));
		await settled();

		expect(deps.loadSchema).not.toHaveBeenCalled();
		expect(published).toEqual([]);
	});

	it('retries a schema request that failed after the configured delay', async () => {
		vi.useFakeTimers();
		const { seed, published, deps } = harness({ deps: { retryDelaysMs: [1000] } });
		vi.mocked(deps.loadSchema).mockRejectedValueOnce(new Error('offline'));
		seed.sync(target());
		await settled();
		expect(deps.loadSchema).toHaveBeenCalledTimes(1);
		expect(published).toEqual([]);

		await vi.advanceTimersByTimeAsync(1000);
		await settled();
		expect(deps.loadSchema).toHaveBeenCalledTimes(2);
		expect(published).toHaveLength(1);
	});

	it('stops retrying once every configured delay is used up', async () => {
		vi.useFakeTimers();
		const { seed, published, deps } = harness({ deps: { retryDelaysMs: [10, 20] } });
		vi.mocked(deps.loadSchema).mockRejectedValue(new Error('offline'));
		seed.sync(target());
		await settled();
		await vi.advanceTimersByTimeAsync(10_000);
		await settled();

		expect(deps.loadSchema).toHaveBeenCalledTimes(3);
		expect(published).toEqual([]);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('leaves a slot empty when its model search fails', async () => {
		const { seed, published, missing } = harness({
			deps: {
				search: vi.fn(async (slot: AutopickSlot) => {
					if (slot.name === 'vae') throw new Error('offline');
					const models = SINGLE[slot.name];
					return { models, total: models.length };
				})
			}
		});
		seed.sync(target());
		await settled();

		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({ vae: '', diffusion_model: 'model:dm-1', text_encoder: 'model:te-1' });
		expect(missing.at(-1)).toEqual({ key: 'zimage-txt2img-', name: 'vae' });
	});

	it('applies the picked cloud model capabilities before publishing', async () => {
		const caps = new Map<string, typeof CLOUD_CAPS>();
		const { seed, published, deps } = harness({
			rows: { diffusion_model: [{ id: 'cloud-1' }] },
			schemas: { txt2img: cloudSchema() },
			deps: {
				loadCapabilities: vi.fn(async (modelId: string) => {
					caps.set(modelId, CLOUD_CAPS);
				}),
				capabilityOf: (modelId) => caps.get(modelId)
			}
		});
		seed.sync(target());
		await settled();

		expect(deps.loadCapabilities).toHaveBeenCalledWith('cloud-1');
		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({ diffusion_model: 'model:cloud-1', steps: 30 });
		expect(published[0]).not.toHaveProperty('guidance');
	});

	it('publishes the newest form data when it changes while the schema is loading', async () => {
		const first = deferred<unknown>();
		const { seed, published, deps } = harness();
		vi.mocked(deps.loadSchema).mockImplementationOnce(() => first.promise);
		const chosen = { diffusion_model: 'model:a', text_encoder: 'model:b', vae: 'model:c' };
		seed.sync(target({ formData: { ...chosen, steps: 11 } }));
		seed.sync(target({ formData: { ...chosen, steps: 22 } }));
		await settled();
		first.resolve(schemaFor());
		await settled();

		expect(published).toHaveLength(1);
		expect(published[0]).toMatchObject({ ...chosen, steps: 22 });
		expect(deps.search).not.toHaveBeenCalled();
	});
});
