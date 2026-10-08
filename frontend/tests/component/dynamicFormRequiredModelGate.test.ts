// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { missingModelByTab, missingModelFor } = await import('$lib/generation/requiredModelField');
const { createClassComponent } = await import('svelte/legacy');

const modelField = { name: 'diffusion_model', type: 'model', title: 'Diffusion Model', required: true };

function schemaWith(children: unknown[]) {
	return { properties: { root: { type: 'row', children } } };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let destroy: (() => void) | undefined;

beforeEach(() => {
	clearSchemaCache();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

function mount(schema: unknown, initialData: Record<string, unknown> = {}) {
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: schema }
	} as never);
	const target = document.createElement('div');
	document.body.appendChild(target);
	const presetId = `native-${Math.random()}`;
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: { presetId, mode: 'txt2img', tabId: 'tab-1', initialData }
	}) as unknown as { $destroy: () => void };
	destroy = () => {
		form.$destroy();
		target.remove();
	};
	return `${presetId}-txt2img-`;
}

describe('DynamicForm required model gate', () => {
	it('reports a required model field that has no value', async () => {
		const key = mount(schemaWith([modelField]));
		await settle();
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toEqual({
			name: 'diffusion_model',
			label: 'Diffusion Model'
		});
	});

	it('reports nothing once the field holds a model', async () => {
		const key = mount(schemaWith([modelField]), { diffusion_model: 'model:abc' });
		await settle();
		expect(get(missingModelByTab)['tab-1']?.key).toBe(key);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('does not require a field the form hides', async () => {
		const key = mount(schemaWith([{ ...modelField, visible: false }]));
		await settle();
		expect(get(missingModelByTab)['tab-1']?.key).toBe(key);
		expect(missingModelFor(get(missingModelByTab), 'tab-1', key)).toBeNull();
	});

	it('forgets the report when the form goes away', async () => {
		mount(schemaWith([modelField]));
		await settle();
		destroy?.();
		destroy = undefined;
		expect(get(missingModelByTab)['tab-1']).toBeUndefined();
	});
});
