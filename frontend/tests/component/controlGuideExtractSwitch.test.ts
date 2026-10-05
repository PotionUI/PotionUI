import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: SelectField } = await import('$lib/components/form-fields/SelectField.svelte');
const { default: CheckboxField } = await import('$lib/components/form-fields/CheckboxField.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { mount, unmount } = await import('svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: Host } = await import('./stubs/GenerationFormPaneHost.svelte');

Element.prototype.scrollIntoView = () => {};
registerFieldComponent('row', { component: RowField });
registerFieldComponent('select', { component: SelectField });
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
		control: {
			type: 'row',
			children: [
				{
					name: 'guide',
					type: 'select',
					title: 'Guide',
					default: 'canny',
					options: [
						{ label: 'None', value: 'none' },
						{ label: 'Canny', value: 'canny' },
						{ label: 'Pose', value: 'openpose' },
						{ label: 'Grayscale', value: 'grayscale' }
					],
					reactions: [reaction('guide', 'equals', 'as_is', { set_value: 'canny' })]
				},
				{
					name: 'guide_extract',
					type: 'boolean',
					title: 'Extract the guide from a photo',
					default: true,
					reactions: [
						reaction('guide', 'in', ['none', 'grayscale'], { set_visibility: false }),
						reaction('guide', 'not_in', ['none', 'grayscale'], { set_visibility: true }),
						reaction('guide', 'equals', 'as_is', { set_value: false })
					]
				}
			]
		}
	}
};

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountForm(formData: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const tabId = tabsStore.addTabWithData('Guide switch', {
		selectedPreset: 'reactions',
		selectedMode: 'catalog',
		formData
	} as never);
	const host = mount(Host, { target, props: { tabId } });
	const field = (name: string) => target.querySelector<HTMLElement>(`[data-field-name="${name}"]`);
	return {
		field,
		choose: async (label: string) => {
			field('guide')!.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.click();
			await settle();
			Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
				.find((el) => el.textContent?.trim() === label)!
				.click();
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

const box = () => mounted!.field('guide_extract')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
const guideLabel = () =>
	mounted!.field('guide')!.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.textContent?.trim();

describe('A guide switched between extracting and using the image as it is', () => {
	it('loads an old "use as is" guide as Canny with extraction off', async () => {
		mounted = mountForm({ guide: 'as_is' });
		await settle();
		expect(guideLabel()).toContain('Canny');
		expect(box().checked).toBe(false);
	});

	it('migrates a saved "use as is" session that still carries extraction on', async () => {
		mounted = mountForm({ guide: 'as_is', guide_extract: true });
		await settle();
		expect(guideLabel()).toContain('Canny');
		expect(box().checked).toBe(false);
	});

	it('hides the switch for None and Grayscale and shows it for a detector', async () => {
		mounted = mountForm();
		await settle();
		expect(box().checked).toBe(true);

		await mounted.choose('None');
		expect(mounted.field('guide_extract')).toBeNull();

		await mounted.choose('Grayscale');
		expect(mounted.field('guide_extract')).toBeNull();

		await mounted.choose('Pose');
		expect(mounted.field('guide_extract')).toBeTruthy();
	});
});
