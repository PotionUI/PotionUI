// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { settle } from './helpers/settle';
import { fire, mountSchemaForm, setRange, typeInto, pickOption, type SchemaFormHandle } from './helpers/schemaForm';

const { api } = await import('$lib/services/api/index');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { buildFormSchema } = await import('$lib/form/formSchema');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');

registerBuiltinFieldComponents();

const FIELDS = [
	{ type: 'string', name: 'label', title: 'Label', default: 'hello' },
	{ type: 'integer', name: 'count', title: 'Count', default: 2, minimum: 0, maximum: 10 },
	{ type: 'slider', name: 'amount', title: 'Amount', default: 30, minimum: 0, maximum: 100, step: 1 },
	{ type: 'boolean', name: 'soft', title: 'Soft', default: false },
	{
		type: 'select',
		name: 'mode',
		title: 'Mode',
		default: 'a',
		options: [
			{ label: 'Alpha', value: 'a' },
			{ label: 'Beta', value: 'b' }
		]
	},
	{
		type: 'segmented',
		name: 'fit',
		title: 'Fit',
		default: 'pad',
		options: [
			{ label: 'Pad', value: 'pad' },
			{ label: 'Cover', value: 'cover' }
		]
	},
	{ type: 'number_box', name: 'size', title: 'Size', minimum: 0, maximum: 4000 }
];

let mounted: SchemaFormHandle | undefined;
let schemaRequest: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	schemaRequest = vi.spyOn(api, 'getPresetFormSchema').mockRejectedValue(new Error('no preset'));
	vi.spyOn(api, 'getPresetModels').mockResolvedValue({ success: false } as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.restoreAllMocks();
});

describe('DynamicForm mounted from a schema prop', () => {
	it('renders the given fields with no preset, no tab and no schema request', async () => {
		const published: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema(FIELDS),
			onFormDataChange: (data: Record<string, unknown>) => published.push(data)
		});

		for (const field of FIELDS) expect(mounted.field(field.name), field.name).toBeTruthy();
		expect(mounted.target.textContent).not.toContain('Loading Form');
		expect(schemaRequest).not.toHaveBeenCalled();
		expect(published[0]).toMatchObject({ label: 'hello', count: 2, amount: 30, soft: false, mode: 'a', fit: 'pad' });
	});

	it('takes initial values from initialData over the field defaults', async () => {
		const published: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema(FIELDS),
			initialData: { amount: 70, label: 'seeded' },
			onFormDataChange: (data: Record<string, unknown>) => published.push(data)
		});

		expect(published[0]).toMatchObject({ amount: 70, label: 'seeded', count: 2 });
		expect(mounted.field('amount')!.querySelector<HTMLInputElement>('input[type="range"]')!.value).toBe('70');
	});

	it('publishes a change for each field type', async () => {
		const published: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema(FIELDS),
			onFormDataChange: (data: Record<string, unknown>) => published.push(data)
		});
		const last = () => published[published.length - 1];

		typeInto(mounted.field('label')!.querySelector('input')!, 'bye');
		expect(last().label).toBe('bye');

		setRange(mounted.field('amount')!.querySelector('input[type="range"]')!, 55);
		expect(last().amount).toBe(55);

		mounted.field('soft')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle(4);
		expect(last().soft).toBe(true);

		await pickOption(mounted.field('mode')!, 'Beta');
		expect(last().mode).toBe('b');

		Array.from(mounted.field('fit')!.querySelectorAll('button'))
			.find((button) => button.textContent?.trim() === 'Cover')!
			.click();
		await settle(4);
		expect(last().fit).toBe('cover');

		typeInto(mounted.field('size')!.querySelector('input')!, '640');
		expect(last().size).toBe(640);

		const countInput = mounted.field('count')!.querySelector<HTMLInputElement>('input[type="number"]')!;
		countInput.value = '5';
		fire(countInput, 'input');
		expect(last().count).toBe(5);
	});

	it('applies reactions of the given schema', async () => {
		const fields = [
			{ type: 'boolean', name: 'advanced', title: 'Advanced', default: false },
			{
				type: 'string',
				name: 'extra',
				title: 'Extra',
				reactions: [
					{
						when: { field: 'advanced', operator: 'equals', value: true, equals: true },
						then: { set_visibility: true }
					},
					{
						when: { field: 'advanced', operator: 'not_equals', value: true, not_equals: true },
						then: { set_visibility: false }
					}
				]
			}
		];
		mounted = await mountSchemaForm(DynamicForm, { schema: buildFormSchema(fields) });
		expect(mounted.field('extra')).toBeNull();

		mounted.field('advanced')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle(6);
		expect(mounted.field('extra')).toBeTruthy();
	});

	it('shows model fields in their unavailable state instead of throwing when there is no preset', async () => {
		const fields = [{ type: 'model', name: 'checkpoint', title: 'Checkpoint', default: '' }];
		mounted = await mountSchemaForm(DynamicForm, { schema: buildFormSchema(fields) });
		await settle(6);

		expect(mounted.field('checkpoint')).toBeTruthy();
		expect(mounted.field('checkpoint')!.textContent).not.toContain('Unsupported field type');
	});

	it('swaps to a new schema and re-hydrates from initialData', async () => {
		const published: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema([FIELDS[0]]),
			onFormDataChange: (data: Record<string, unknown>) => published.push(data)
		});
		expect(mounted.field('label')).toBeTruthy();

		mounted.set({ schema: buildFormSchema([FIELDS[2]]) });
		await settle(6);

		expect(mounted.field('label')).toBeNull();
		expect(mounted.field('amount')).toBeTruthy();
		expect(published[published.length - 1]).toEqual({ amount: 30 });
	});

	it('ignores a new schema object with the same content', async () => {
		const published: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema(FIELDS),
			onFormDataChange: (data: Record<string, unknown>) => published.push(data)
		});
		setRange(mounted.field('amount')!.querySelector('input[type="range"]')!, 80);
		const before = published.length;

		mounted.set({ schema: buildFormSchema(FIELDS) });
		await settle(6);

		expect(published.length).toBe(before);
		expect(mounted.field('amount')!.querySelector<HTMLInputElement>('input[type="range"]')!.value).toBe('80');
	});

	it('leaves the preset-driven path untouched when no schema is given', async () => {
		schemaRequest.mockResolvedValue({
			success: true,
			data: { preset_id: 'p', form_schema: buildFormSchema([FIELDS[0]]) }
		} as never);
		mounted = await mountSchemaForm(DynamicForm, { presetId: `schema-prop-${Math.random()}`, mode: 'txt2img' });
		await settle(8);

		expect(schemaRequest).toHaveBeenCalledTimes(1);
		expect(mounted.field('label')).toBeTruthy();
	});

	it('does not request a schema when both a preset and a schema are given', async () => {
		mounted = await mountSchemaForm(DynamicForm, {
			presetId: `schema-prop-${Math.random()}`,
			mode: 'txt2img',
			schema: buildFormSchema([FIELDS[0]])
		});
		await settle(6);

		expect(schemaRequest).not.toHaveBeenCalled();
		expect(mounted.field('label')).toBeTruthy();
	});
});
