// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { settle } from './helpers/settle';
import { fire, mountSchemaForm, press, setRange, typeInto, pickOption, type SchemaFormHandle } from './helpers/schemaForm';

const { api } = await import('$lib/services/api/index');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { buildFormSchema } = await import('$lib/form/formSchema');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { default: PlainProbeField } = await import('./stubs/PlainProbeField.svelte');
const { default: SelfCommitProbeField } = await import('./stubs/SelfCommitProbeField.svelte');

registerBuiltinFieldComponents();
registerFieldComponent('plain_probe', { component: PlainProbeField });
registerFieldComponent('self_probe', { component: SelfCommitProbeField });

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
		],
		reactions: []
	},
	{ type: 'number_box', name: 'size', title: 'Size', minimum: 0, maximum: 4000 },
	{ type: 'plain_probe', name: 'plain', title: 'Plain', default: 0 },
	{ type: 'self_probe', name: 'selfish', title: 'Selfish', default: 0 },
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
	}
];

type Commit = { data: Record<string, unknown>; name: string };

let mounted: SchemaFormHandle | undefined;
let commits: Commit[];

async function mountWithCommit(extra: Record<string, unknown> = {}) {
	commits = [];
	mounted = await mountSchemaForm(DynamicForm, {
		schema: buildFormSchema(FIELDS),
		commitIdleMs: 30,
		onFormDataCommit: (data: Record<string, unknown>, info: { name: string }) => commits.push({ data, name: info.name }),
		...extra
	});
	return mounted;
}

const range = () => mounted!.field('amount')!.querySelector<HTMLInputElement>('input[type="range"]')!;

beforeEach(() => {
	vi.spyOn(api, 'getPresetFormSchema').mockRejectedValue(new Error('no preset'));
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.restoreAllMocks();
});

describe('onFormDataCommit', () => {
	it('fires once for a slider drag however many changes it made', async () => {
		const changes: Array<Record<string, unknown>> = [];
		await mountWithCommit({ onFormDataChange: (data: Record<string, unknown>) => changes.push(data) });
		const before = changes.length;

		for (const value of [31, 35, 40, 48, 55, 61]) setRange(range(), value);
		await settle(4);

		expect(changes.length - before).toBe(6);
		expect(commits).toHaveLength(0);

		fire(range(), 'change');
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0].name).toBe('amount');
		expect(commits[0].data.amount).toBe(61);
	});

	it('waits for the key to come up when a held arrow key repeats the slider change', async () => {
		await mountWithCommit();

		press(range(), 'keydown', 'ArrowRight');
		setRange(range(), 31);
		fire(range(), 'change');
		press(range(), 'keydown', 'ArrowRight');
		setRange(range(), 32);
		fire(range(), 'change');
		await settle(4);
		expect(commits).toHaveLength(0);

		press(range(), 'keyup', 'ArrowRight');
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0].data.amount).toBe(32);
	});

	it('fires once for a select choice with the reaction-settled data', async () => {
		await mountWithCommit();

		await pickOption(mounted!.field('mode')!, 'Beta');
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0].name).toBe('mode');
		expect(commits[0].data.mode).toBe('b');
		expect(commits[0].data.follower).toBe(7);
	});

	it('fires once per toggle', async () => {
		await mountWithCommit();
		const box = mounted!.field('soft')!.querySelector<HTMLInputElement>('input[type="checkbox"]')!;

		box.click();
		await settle(4);
		box.click();
		await settle(4);

		expect(commits.map((commit) => [commit.name, commit.data.soft])).toEqual([
			['soft', true],
			['soft', false]
		]);
	});

	it('fires for a text field on change or blur, not while typing', async () => {
		await mountWithCommit();
		const input = mounted!.field('label')!.querySelector('input')!;

		typeInto(input, 'h');
		typeInto(input, 'he');
		typeInto(input, 'hey');
		await settle(4);
		expect(commits).toHaveLength(0);

		fire(input, 'change');
		input.blur();
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0]).toMatchObject({ name: 'label', data: { label: 'hey' } });
	});

	it('fires on Enter in a text field and not a second time on the blur after it', async () => {
		await mountWithCommit();
		const input = mounted!.field('label')!.querySelector('input')!;

		typeInto(input, 'done');
		press(input, 'keydown', 'Enter');
		input.blur();
		fire(input, 'change');
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0].data.label).toBe('done');
	});

	it('fires for a number field on Enter and for a stepper press', async () => {
		await mountWithCommit();
		const field = mounted!.field('count')!;
		const input = field.querySelector<HTMLInputElement>('input[type="number"]')!;

		input.value = '6';
		fire(input, 'input');
		await settle(4);
		expect(commits).toHaveLength(0);
		press(input, 'keydown', 'Enter');
		await settle(4);
		expect(commits).toHaveLength(1);
		expect(commits[0].data.count).toBe(6);

		field.querySelector<HTMLButtonElement>('button[aria-label="Increase value"]')!.click();
		await settle(4);
		expect(commits).toHaveLength(2);
		expect(commits[1].data.count).toBe(7);
	});

	it('fires for a number box on blur', async () => {
		await mountWithCommit();
		const input = mounted!.field('size')!.querySelector('input')!;

		typeInto(input, '640');
		await settle(4);
		expect(commits).toHaveLength(0);
		input.blur();
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0]).toMatchObject({ name: 'size', data: { size: 640 } });
	});

	it('commits a field that never calls onCommit after a short idle, once per burst', async () => {
		await mountWithCommit();
		const bump = mounted!.field('plain')!.querySelector<HTMLButtonElement>('[data-probe-bump]')!;

		bump.click();
		bump.click();
		bump.click();
		await settle(2);
		expect(commits).toHaveLength(0);

		await new Promise((resolve) => setTimeout(resolve, 80));
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0]).toMatchObject({ name: 'plain', data: { plain: 3 } });
	});

	it('does not add an idle commit for a field that reports its own', async () => {
		await mountWithCommit();
		const bump = mounted!.field('selfish')!.querySelector<HTMLButtonElement>('[data-probe-bump]')!;

		bump.click();
		await settle(4);
		await new Promise((resolve) => setTimeout(resolve, 80));
		await settle(4);

		expect(commits).toHaveLength(1);
		expect(commits[0].name).toBe('selfish');
	});

	it('never schedules idle commits for a committing field mid-interaction', async () => {
		await mountWithCommit();

		setRange(range(), 40);
		await new Promise((resolve) => setTimeout(resolve, 80));
		setRange(range(), 50);
		await new Promise((resolve) => setTimeout(resolve, 80));
		await settle(4);

		expect(commits).toHaveLength(0);
	});

	it('drops a pending idle commit when the form is destroyed', async () => {
		await mountWithCommit();
		mounted!.field('plain')!.querySelector<HTMLButtonElement>('[data-probe-bump]')!.click();

		mounted!.destroy();
		mounted = undefined;
		await new Promise((resolve) => setTimeout(resolve, 80));

		expect(commits).toHaveLength(0);
	});

	it('leaves a form without the callback working as before', async () => {
		const changes: Array<Record<string, unknown>> = [];
		mounted = await mountSchemaForm(DynamicForm, {
			schema: buildFormSchema(FIELDS),
			onFormDataChange: (data: Record<string, unknown>) => changes.push(data)
		});

		setRange(range(), 44);
		fire(range(), 'change');
		mounted.field('plain')!.querySelector<HTMLButtonElement>('[data-probe-bump]')!.click();
		await settle(4);

		expect(changes[changes.length - 1]).toMatchObject({ amount: 44, plain: 1 });
	});
});

