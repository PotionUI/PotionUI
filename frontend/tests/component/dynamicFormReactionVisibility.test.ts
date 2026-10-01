import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: SelectField } = await import('$lib/components/form-fields/SelectField.svelte');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: CheckboxField } = await import('$lib/components/form-fields/CheckboxField.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { mount, unmount } = await import('svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: Host } = await import('./stubs/GenerationFormPaneHost.svelte');

Element.prototype.scrollIntoView = () => {};
registerFieldComponent('row', { component: RowField });
registerFieldComponent('select', { component: SelectField });
registerFieldComponent('string', { component: TextInput });
registerFieldComponent('boolean', { component: CheckboxField });

function reaction(field: string, operator: string, value: unknown, then: Record<string, unknown>) {
	return {
		when: { field, operator, value, [operator]: value },
		then: {
			set_visibility: null, set_value: null, set_disabled: null, update_options: null,
			update_validation: null, set_filter_tags: null, ...then
		}
	};
}

const SCHEMA = {
	properties: {
		reactions: {
			type: 'row',
			children: [
				{
					name: 'switch',
					type: 'select',
					title: 'Switch',
					default: 'hide',
					options: [
						{ label: 'Hide', value: 'hide' },
						{ label: 'Reveal', value: 'reveal' }
					]
				},
				{
					name: 'toggle',
					type: 'boolean',
					title: 'Toggle',
					default: false,
					reactions: [
						reaction('switch', 'equals', 'hide', { set_visibility: false, set_value: false }),
						reaction('switch', 'not_equals', 'hide', { set_visibility: true })
					]
				},
				{
					name: 'by_switch',
					type: 'string',
					title: 'Shown by the switch',
					reactions: [
						reaction('switch', 'equals', 'reveal', { set_visibility: true }),
						reaction('switch', 'not_equals', 'reveal', { set_visibility: false })
					]
				},
				{
					name: 'by_toggle',
					type: 'string',
					title: 'Shown by the toggle',
					reactions: [
						reaction('toggle', 'equals', true, { set_visibility: true }),
						reaction('toggle', 'not_equals', true, { set_visibility: false })
					]
				}
			]
		}
	}
};

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountForm() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const tabId = tabsStore.addTabWithData('Reactions', {
		selectedPreset: 'reactions',
		selectedMode: 'catalog',
		formData: {}
	} as never);
	const host = mount(Host, { target, props: { tabId } });
	const field = (name: string) => target.querySelector<HTMLElement>(`[data-field-name="${name}"]`);
	return {
		field,
		choose: async (label: string) => {
			field('switch')!.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.click();
			await settle();
			Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
				.find((el) => el.textContent?.trim() === label)!
				.click();
			await settle();
		},
		toggle: async () => {
			field('toggle')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
			await settle();
		},
		destroy: () => {
			unmount(host);
			tabsStore.removeTab(tabId);
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountForm> | undefined;

beforeEach(() => {
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({ success: true, data: { preset_id: 'p', form_schema: SCHEMA } } as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('Reaction visibility on the Generate form follows the value just chosen', () => {
	it('shows and hides on every change of a select, not one change late', async () => {
		mounted = mountForm();
		await settle();
		expect(mounted.field('by_switch')).toBeNull();

		await mounted.choose('Reveal');
		expect(mounted.field('by_switch')).toBeTruthy();
		expect(mounted.field('toggle')).toBeTruthy();

		await mounted.choose('Hide');
		expect(mounted.field('by_switch')).toBeNull();
		expect(mounted.field('toggle')).toBeNull();

		await mounted.choose('Reveal');
		expect(mounted.field('by_switch')).toBeTruthy();
	});

	it('shows a field exactly while its checkbox is ticked, and the box shows the stored value', async () => {
		mounted = mountForm();
		await settle();
		await mounted.choose('Reveal');
		const box = () => mounted!.field('toggle')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!;

		await mounted.toggle();
		expect(box().checked).toBe(true);
		expect(mounted.field('by_toggle')).toBeTruthy();

		await mounted.toggle();
		expect(box().checked).toBe(false);
		expect(mounted.field('by_toggle')).toBeNull();

		await mounted.toggle();
		expect(mounted.field('by_toggle')).toBeTruthy();
	});

	it('applies a value a reaction sets before deciding what is visible', async () => {
		mounted = mountForm();
		await settle();
		await mounted.choose('Reveal');
		await mounted.toggle();
		expect(mounted.field('by_toggle')).toBeTruthy();

		await mounted.choose('Hide');
		expect(mounted.field('toggle')).toBeNull();
		expect(mounted.field('by_toggle')).toBeNull();
	});
});
