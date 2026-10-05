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

function onProfile(value: string, then: Record<string, unknown>) {
	return {
		when: { field: 'profile', equals: value },
		then: {
			set_visibility: null, set_value: null, set_disabled: null, update_options: null,
			update_validation: null, set_filter_tags: null, ...then
		}
	};
}

const SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [
				{ name: 'profile', type: 'string', title: 'Profile', default: 'turbo' },
				{
					name: 'sampler',
					type: 'string',
					title: 'Sampler',
					default: 'euler',
					reactions: [
						onProfile('turbo', { set_value: 'euler' }),
						onProfile('quality', { set_value: 'dpmpp_2m' })
					]
				}
			]
		}
	}
};

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let cleanups: Array<() => void> = [];

function mountForm(initialData?: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const log: Array<Record<string, any>> = [];
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: {
			presetId: 'reaction-user-choice',
			mode: 'txt2img',
			initialData,
			onFormDataChange: (data: Record<string, unknown>) => log.push(data)
		}
	});
	const handle = {
		form,
		log,
		last: () => log[log.length - 1],
		input: (name: string) => target.querySelector<HTMLInputElement>(`[data-field-name="${name}"] input`)!,
		async edit(name: string, value: string) {
			const el = handle.input(name);
			el.value = value;
			el.dispatchEvent(new Event('input', { bubbles: true }));
			el.dispatchEvent(new Event('change', { bubbles: true }));
			await settle();
		},
		unmount() {
			form.$destroy();
			target.remove();
		}
	};
	cleanups.push(handle.unmount);
	return handle;
}

beforeEach(() => {
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: SCHEMA }
	} as never);
});

afterEach(() => {
	for (const fn of cleanups) {
		try {
			fn();
		} catch {}
	}
	cleanups = [];
});

describe('set_value reactions versus the user choice', () => {
	it('applies the reaction defaults on a fresh form', async () => {
		const form = mountForm();
		await settle();
		expect(form.last().sampler).toBe('euler');
	});

	it('keeps an edit of the target while the trigger value is unchanged', async () => {
		const form = mountForm();
		await settle();
		await form.edit('sampler', 'euler_sde');
		expect(form.last().sampler).toBe('euler_sde');
		await form.edit('sampler', 'euler_sde_2');
		expect(form.last().sampler).toBe('euler_sde_2');
	});

	it('keeps the user choice across a remount with the saved data', async () => {
		const first = mountForm();
		await settle();
		await first.edit('sampler', 'euler_sde');
		const saved = first.last();
		first.unmount();

		const second = mountForm(saved);
		await settle();
		expect(second.last().sampler).toBe('euler_sde');
		expect(second.input('sampler').value).toBe('euler_sde');
	});

	it('keeps the user choice when a saved session is loaded into a mounted form', async () => {
		const form = mountForm();
		await settle();
		form.form.$set({ initialData: { profile: 'turbo', sampler: 'euler_sde' } });
		await settle();
		expect(form.last().sampler).toBe('euler_sde');
	});

	it('keeps the user choice across repeated remounts like the tab switch', async () => {
		const first = mountForm();
		await settle();
		await first.edit('sampler', 'euler_sde');
		let saved: Record<string, any> = first.last();
		first.unmount();
		for (let i = 0; i < 3; i++) {
			const next = mountForm(saved);
			await settle();
			saved = next.last();
			next.unmount();
		}
		expect(saved.sampler).toBe('euler_sde');
	});

	it('still applies the sampler when the user changes the profile', async () => {
		const form = mountForm();
		await settle();
		await form.edit('sampler', 'euler_sde');
		await form.edit('profile', 'quality');
		expect(form.last().sampler).toBe('dpmpp_2m');
		await form.edit('profile', 'turbo');
		expect(form.last().sampler).toBe('euler');
	});

	it('still applies the profile sampler after a remount', async () => {
		const first = mountForm();
		await settle();
		await first.edit('sampler', 'euler_sde');
		const saved = first.last();
		first.unmount();
		const second = mountForm(saved);
		await settle();
		await second.edit('profile', 'quality');
		expect(second.last().sampler).toBe('dpmpp_2m');
	});
});
