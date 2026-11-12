// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';
import { mountComponent } from './helpers/mount';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { missingModelByTab, missingModelFor } = await import('$lib/generation/requiredModelField');

const modelField = { name: 'diffusion_model', type: 'model', title: 'Diffusion Model', required: true };

function schemaWith(children: unknown[]) {
	return { properties: { root: { type: 'row', children } } };
}

let destroy: (() => void) | undefined;

beforeEach(() => {
	clearSchemaCache();
});

function mount(schema: unknown, initialData: Record<string, unknown> = {}) {
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: schema }
	} as never);
	const presetId = `native-${Math.random()}`;
	destroy = mountComponent(DynamicForm, { presetId, mode: 'txt2img', tabId: 'tab-1', initialData }).destroy;
	return `${presetId}-txt2img-`;
}

describe('DynamicForm required model gate', () => {
	it('reports a required model field that has no value', async () => {
		const key = mount(schemaWith([modelField]));
		await settle(10);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toEqual({
			name: 'diffusion_model',
			label: 'Diffusion Model'
		});
	});

	it('reports nothing once the field holds a model', async () => {
		const key = mount(schemaWith([modelField]), { diffusion_model: 'model:abc' });
		await settle(10);
		await new Promise((resolve) => setTimeout(resolve, 400));
		await settle(10);
		expect(get(missingModelByTab)['tab-1']?.key).toBe(key);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('does not require a field the form hides', async () => {
		const key = mount(schemaWith([{ ...modelField, visible: false }]));
		await settle(10);
		expect(get(missingModelByTab)['tab-1']?.key).toBe(key);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('forgets the report when the form goes away', async () => {
		mount(schemaWith([modelField]));
		await settle(10);
		destroy?.();
		expect(get(missingModelByTab)['tab-1']).toBeUndefined();
	});
});
