// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

registerFieldComponent('string', { component: TextInput });
registerFieldComponent('row', { component: RowField });

const SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [{ name: 'prompt', type: 'string', title: 'Prompt', default: 'first' }]
		}
	}
};

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let destroy: (() => void) | undefined;

beforeEach(() => {
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: SCHEMA }
	} as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

describe('A form without capability bindings', () => {
	it('applies an initialData identical to an earlier publish', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const log: Array<Record<string, unknown>> = [];
		let form: { $set: (props: Record<string, unknown>) => void; $destroy: () => void };
		form = createClassComponent({
			component: DynamicForm as never,
			target,
			props: {
				presetId: `native-${Math.random()}`,
				mode: 'txt2img',
				onFormDataChange: (data: Record<string, unknown>) => {
					log.push(data);
					queueMicrotask(() => form.$set({ initialData: data }));
				}
			}
		});
		destroy = () => {
			form.$destroy();
			target.remove();
		};
		await settle();
		const first = log[log.length - 1];
		expect(first.prompt).toBe('first');

		const input = target.querySelector<HTMLInputElement>('[data-field-name="prompt"] input')!;
		input.value = 'second';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(log[log.length - 1].prompt).toBe('second');

		form.$set({ initialData: first });
		await settle();

		expect(log[log.length - 1].prompt).toBe('first');
		expect(target.querySelector<HTMLInputElement>('[data-field-name="prompt"] input')!.value).toBe('first');
	});
});
