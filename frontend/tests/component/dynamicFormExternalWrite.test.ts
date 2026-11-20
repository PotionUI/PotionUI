// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { settle } from './helpers/settle';
import { mountSchemaForm, setRange, typeInto, type SchemaFormHandle } from './helpers/schemaForm';

const { api } = await import('$lib/services/api/index');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { buildFormSchema } = await import('$lib/form/formSchema');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');

registerBuiltinFieldComponents();

const FIELDS = [
	{ type: 'string', name: 'label', title: 'Label', default: 'hello' },
	{ type: 'number_box', name: 'x', title: 'X', default: 10 },
	{ type: 'number_box', name: 'y', title: 'Y', default: 20 },
	{ type: 'slider', name: 'amount', title: 'Amount', default: 30, minimum: 0, maximum: 100, step: 1 },
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
		type: 'number',
		name: 'follower',
		title: 'Follower',
		default: 0,
		reactions: [
			{
				when: { field: 'mode', operator: 'equals', value: 'b', equals: 'b' },
				then: { set_value: 7 }
			}
		]
	},
	{
		type: 'string',
		name: 'extra',
		title: 'Extra',
		reactions: [
			{
				when: { field: 'mode', operator: 'equals', value: 'b', equals: 'b' },
				then: { set_visibility: true }
			},
			{
				when: { field: 'mode', operator: 'not_equals', value: 'b', not_equals: 'b' },
				then: { set_visibility: false }
			}
		]
	}
];

let mounted: SchemaFormHandle | undefined;
let changes: Array<Record<string, unknown>>;
let commits: Array<{ data: Record<string, unknown>; name: string }>;

async function mountForm(extra: Record<string, unknown> = {}) {
	changes = [];
	commits = [];
	mounted = await mountSchemaForm(DynamicForm, {
		schema: buildFormSchema(FIELDS),
		commitIdleMs: 20,
		onFormDataChange: (data: Record<string, unknown>) => changes.push(data),
		onFormDataCommit: (data: Record<string, unknown>, info: { name: string }) => commits.push({ data, name: info.name }),
		...extra
	});
	return mounted;
}

const input = (name: string) => mounted!.field(name)!.querySelector('input')!;
const range = () => mounted!.field('amount')!.querySelector<HTMLInputElement>('input[type="range"]')!;

beforeEach(() => {
	vi.spyOn(api, 'getPresetFormSchema').mockRejectedValue(new Error('no preset'));
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.restoreAllMocks();
});

describe('DynamicForm.setValues', () => {
	it('shows the written values without publishing or committing them back', async () => {
		await mountForm();
		const published = changes.length;

		expect(mounted!.form.setValues({ x: 120, y: 80, amount: 77 })).toBe(true);
		await settle(6);
		await new Promise((resolve) => setTimeout(resolve, 60));

		expect(input('x').value).toBe('120');
		expect(input('y').value).toBe('80');
		expect(range().value).toBe('77');
		expect(changes.length).toBe(published);
		expect(commits).toHaveLength(0);
	});

	it('leaves every other field as the user left it', async () => {
		await mountForm({ initialData: { label: 'seeded' } });
		typeInto(input('label'), 'typed by hand');
		setRange(range(), 61);
		await settle(4);

		mounted!.form.setValues({ x: 5 });
		await settle(6);
		setRange(range(), 62);
		await settle(4);

		expect(input('label').value).toBe('typed by hand');
		expect(changes[changes.length - 1]).toMatchObject({ label: 'typed by hand', amount: 62, x: 5, y: 20 });
	});

	it('does not run initial hydration again', async () => {
		await mountForm({ initialData: { label: 'seeded', amount: 50 } });
		typeInto(input('label'), 'edited');
		await settle(4);
		const published = changes.length;

		mounted!.form.setValues({ amount: 90 });
		await settle(6);

		expect(input('label').value).toBe('edited');
		expect(changes.length).toBe(published);
	});

	it('still publishes the next user change as a change', async () => {
		await mountForm();
		mounted!.form.setValues({ x: 300 });
		await settle(6);
		const published = changes.length;

		setRange(range(), 12);
		await settle(4);

		expect(changes.length).toBe(published + 1);
		expect(changes[changes.length - 1]).toMatchObject({ x: 300, amount: 12 });
	});

	it('publishes a later user edit of a field it wrote earlier', async () => {
		await mountForm();
		mounted!.form.setValues({ x: 300 });
		await settle(6);
		const published = changes.length;

		typeInto(input('x'), '55');
		await settle(4);

		expect(changes.length).toBe(published + 1);
		expect(changes[changes.length - 1]).toMatchObject({ x: 55 });
	});

	it('runs the reactions of the field it wrote and publishes what they derived', async () => {
		await mountForm();
		expect(mounted!.field('extra')).toBeNull();
		const published = changes.length;

		mounted!.form.setValues({ mode: 'b' });
		await settle(8);

		expect(mounted!.field('extra')).toBeTruthy();
		expect(changes.length).toBe(published + 1);
		expect(changes[changes.length - 1]).toMatchObject({ mode: 'b', follower: 7 });
		expect(commits).toHaveLength(0);
	});

	it('ignores a write that changes nothing', async () => {
		await mountForm();
		const published = changes.length;

		expect(mounted!.form.setValues({ x: 10, amount: 30 })).toBe(true);
		await settle(6);

		expect(changes.length).toBe(published);
	});

	it('refuses a write before the form has a schema', async () => {
		vi.spyOn(api, 'getPresetFormSchema').mockReturnValue(new Promise(() => {}));
		changes = [];
		mounted = await mountSchemaForm(DynamicForm, { presetId: `pending-${Math.random()}`, mode: 'txt2img' });

		expect(mounted.form.setValues({ x: 1 })).toBe(false);
	});

	it('does not loop when the caller mirrors every change into a write', async () => {
		let armed = false;
		let writes = 0;
		await mountForm();
		mounted!.set({
			onFormDataChange: (data: Record<string, unknown>) => {
				changes.push(data);
				if (!armed) return;
				writes += 1;
				mounted!.form.setValues({ x: Number(data.amount) * 2 });
			}
		});
		armed = true;

		setRange(range(), 21);
		await settle(8);
		setRange(range(), 22);
		await settle(8);

		expect(writes).toBe(2);
		expect(input('x').value).toBe('44');
	});
});
