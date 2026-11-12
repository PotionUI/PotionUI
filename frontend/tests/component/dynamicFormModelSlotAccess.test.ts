// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';
import { mountComponent } from './helpers/mount';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getPresetFormSchema: vi.fn(),
		getPresetModels: vi.fn(),
		getModelById: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { forgetClearedSlots } = await import('$lib/form/modelSlotAutopick');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { missingModelByTab, missingModelFor, missingModelMessage } = await import('$lib/generation/requiredModelField');
const { toasts } = await import('$lib/stores/toast');

const ADMINS = 'admins-dit.safetensors';
const mine = (id: string) => ({ id, filename: `${id}.safetensors`, model_type: 'diffusion_model' });

function slot(extra: Record<string, unknown> = {}) {
	return {
		name: 'diffusion_model',
		type: 'model',
		title: 'MiniMax-H3 DiT',
		required: true,
		configuration: { model_type: 'diffusion_model', families: ['minimax_h3'] },
		...extra
	};
}

let destroy: (() => void) | undefined;
let published: Array<Record<string, any>>;

function userOptions(options: Array<ReturnType<typeof mine>>, installedNames: string[] = []) {
	vi.mocked(api.getPresetModels).mockImplementation((async (_preset: string, _type: string, search?: string) => {
		const models = search ? options.filter((m) => installedNames.includes(m.filename) && search.includes(m.id)) : options;
		return { success: true, data: { models, total: models.length } };
	}) as never);
}

function open(field: Record<string, unknown>, initialData: Record<string, unknown> = {}) {
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: { properties: { root: { type: 'row', children: [field] } } } }
	} as never);
	const presetId = `native-${Math.random()}`;
	destroy = mountComponent(DynamicForm, {
		presetId,
		mode: 'txt2img',
		tabId: 'tab-1',
		initialData,
		onFormDataChange: (data: Record<string, any>) => published.push(data)
	}).destroy;
	return `${presetId}-txt2img-`;
}

const ready = async () => {
	await settle(5);
	await new Promise((resolve) => setTimeout(resolve, 400));
	await settle(10);
};
const lastValue = () => published.at(-1)?.diffusion_model;
const wire = (value: unknown) => (value && typeof value === 'object' ? (value as { modelPath: string }).modelPath : value);

beforeEach(() => {
	clearSchemaCache();
	forgetClearedSlots();
	published = [];
	vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
	vi.mocked(api.getModelById).mockResolvedValue({ success: false, error: 'model_not_found' } as never);
	for (const toast of get(toasts)) toasts.remove(toast.id);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

describe('a regular user opening a form whose default model is not theirs', () => {
	it('swaps in their only matching model and calls the form ready', async () => {
		userOptions([mine('mine')]);
		const key = open(slot({ default: ADMINS }));
		await ready();

		expect(wire(lastValue())).toBe('model:mine');
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('empties the slot and names it when several of their models match', async () => {
		userOptions([mine('a'), mine('b')]);
		const key = open(slot({ default: ADMINS }));
		await ready();

		expect(wire(lastValue()) ?? '').toBe('');
		const missing = missingModelFor(get(missingModelByTab), 'tab-1', key);
		expect(missing).toEqual({ name: 'diffusion_model', label: 'MiniMax-H3 DiT' });
		expect(missingModelMessage(missing!)).toBe('Select MiniMax-H3 DiT to generate');
	});

	it('empties the slot and names it when none of their models match', async () => {
		userOptions([]);
		const key = open(slot({ default: ADMINS }));
		await ready();

		expect(wire(lastValue()) ?? '').toBe('');
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)?.name).toBe('diffusion_model');
	});

	it('keeps the default when it is one of their models', async () => {
		userOptions([mine('admins-dit'), mine('other')], [`admins-dit.safetensors`]);
		const key = open(slot({ default: ADMINS }));
		await ready();

		expect(wire(lastValue())).toBe(ADMINS);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('is not ready while the default is still being checked', async () => {
		let release: () => void = () => {};
		const gate = new Promise<void>((resolve) => (release = resolve));
		vi.mocked(api.getPresetModels).mockImplementation((async (_p: string, _t: string, search?: string) => {
			if (search) await gate;
			return { success: true, data: { models: [mine('admins-dit')], total: 1 } };
		}) as never);
		const key = open(slot({ default: ADMINS }));
		await ready();

		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)?.name).toBe('diffusion_model');
		release();
		await ready();
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});
});

describe('a value restored from a saved session', () => {
	it('is cleared with a notice when the user can no longer use it', async () => {
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: { id: 'gone', filename: 'gone.safetensors' } }
		} as never);
		userOptions([mine('mine')]);
		const key = open(slot(), { diffusion_model: 'model:gone' });
		await ready();

		expect(wire(lastValue()) ?? '').toBe('');
		expect(get(toasts).some((t) => t.type === 'warning' && t.message.includes('MiniMax-H3 DiT'))).toBe(true);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)?.name).toBe('diffusion_model');
	});

	it('is kept when the user can use it', async () => {
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: mine('mine') }
		} as never);
		userOptions([mine('mine')], ['mine.safetensors']);
		const key = open(slot(), { diffusion_model: 'model:mine' });
		await ready();

		expect(wire(lastValue())).toBe('model:mine');
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
		expect(get(toasts)).toHaveLength(0);
	});

	it('is kept when the check itself fails', async () => {
		vi.mocked(api.getModelById).mockRejectedValue(new Error('offline'));
		userOptions([mine('mine')]);
		const key = open(slot(), { diffusion_model: 'model:mine' });
		await ready();

		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
		expect(get(toasts)).toHaveLength(0);
	});

	it('empty slot restored from a saved session is filled when one model fits', async () => {
		userOptions([mine('mine')]);
		open(slot(), { diffusion_model: '' });
		await ready();

		expect(wire(lastValue())).toBe('model:mine');
	});
});
