import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: TabsField } = await import('$lib/components/form-fields/TabsField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

registerFieldComponent('string', { component: TextInput });
registerFieldComponent('tabs', { component: TabsField });

const SCHEMA = {
	properties: {
		layout: {
			type: 'tabs',
			children: [
				{ type: 'tab', name: 'gen', label: 'Generation', children: [{ name: 'steps', type: 'string', title: 'Steps' }] },
				{ type: 'tab', name: 'refs', label: 'References', children: [{ name: 'references', type: 'string', title: 'Refs' }] }
			]
		}
	}
};

const OK = { success: true, data: { preset_id: 'p', form_schema: SCHEMA } } as never;
const FAIL = { success: false, error: 'backend busy' } as never;

let destroy: (() => void) | undefined;

function mountForm() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: { presetId: `retry-${Math.random()}`, mode: 'refs', initialData: {} }
	});
	destroy = () => {
		form.$destroy();
		target.remove();
	};
	return {
		tabLabels: () => Array.from(target.querySelectorAll('[role="tab"]')).map((b) => b.getAttribute('aria-label')),
		text: () => target.textContent ?? ''
	};
}

async function advance(ms: number) {
	await vi.advanceTimersByTimeAsync(ms);
}

describe('DynamicForm recovers from a failed schema load', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		clearSchemaCache();
		vi.mocked(api.getPresetFormSchema).mockReset();
	});

	afterEach(() => {
		destroy?.();
		destroy = undefined;
		vi.useRealTimers();
	});

	it('shows the form tabs on its own once a transient failure clears', async () => {
		vi.mocked(api.getPresetFormSchema).mockResolvedValueOnce(FAIL).mockResolvedValue(OK);
		const view = mountForm();
		await advance(10);
		expect(view.text()).toContain('Could not load form schema');
		expect(view.tabLabels()).toEqual([]);

		await advance(1100);

		expect(view.tabLabels()).toEqual(['Generation', 'References']);
		expect(view.text()).not.toContain('Could not load form schema');
		expect(api.getPresetFormSchema).toHaveBeenCalledTimes(2);
	});

	it('keeps retrying with growing waits and then stops, leaving the Retry button', async () => {
		vi.mocked(api.getPresetFormSchema).mockResolvedValue(FAIL);
		const view = mountForm();
		await advance(20000);
		expect(api.getPresetFormSchema).toHaveBeenCalledTimes(5);
		await advance(60000);
		expect(api.getPresetFormSchema).toHaveBeenCalledTimes(5);
		expect(view.text()).toContain('Could not load form schema');
		expect(view.text()).toContain('Retry');
	});

	it('stops retrying once the form is gone', async () => {
		vi.mocked(api.getPresetFormSchema).mockResolvedValue(FAIL);
		mountForm();
		await advance(10);
		destroy?.();
		destroy = undefined;
		await advance(30000);
		expect(api.getPresetFormSchema).toHaveBeenCalledTimes(1);
	});
});
