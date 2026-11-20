// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { settle } from './helpers/settle';
import { fire, mountSchemaForm, setRange, type SchemaFormHandle } from './helpers/schemaForm';
import { probeUpdates } from './stubs/probeCounter';

const formState = await import('$lib/form/formState');
const { api } = await import('$lib/services/api/index');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { buildFormSchema } = await import('$lib/form/formSchema');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { default: CountingProbeField } = await import('./stubs/CountingProbeField.svelte');

registerBuiltinFieldComponents();
registerFieldComponent('counting_probe', { component: CountingProbeField });

const fields = [
	{ type: 'slider', name: 'amount', title: 'Amount', default: 10, minimum: 0, maximum: 100, fill: 'from-default', layout: 'inline' },
	{ type: 'counting_probe', name: 'p1', title: 'P1', default: 0 },
	{ type: 'counting_probe', name: 'p2', title: 'P2', default: 0 },
	{ type: 'counting_probe', name: 'p3', title: 'P3', default: 0 }
];
const schema = buildFormSchema(fields);

let mounted: SchemaFormHandle | undefined;

let settleSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	settleSpy = vi.spyOn(formState, 'settleFormReactions');
	vi.spyOn(api, 'getPresetFormSchema').mockRejectedValue(new Error('no preset'));
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.restoreAllMocks();
});

async function drag(withCommit: boolean, inputs: number) {
	const publishes: unknown[] = [];
	const commits: unknown[] = [];
	mounted = await mountSchemaForm(DynamicForm, {
		schema,
		onFormDataChange: (data: unknown) => publishes.push(data),
		...(withCommit ? { onFormDataCommit: (data: unknown, info: unknown) => commits.push(info) } : {})
	});
	const range = mounted.field('amount')!.querySelector<HTMLInputElement>('input[type="range"]')!;
	await settle(4);
	settleSpy.mockClear();
	probeUpdates.count = 0;
	publishes.length = 0;
	for (let i = 1; i <= inputs; i++) setRange(range, 10 + i);
	await settle(4);
	const during = { settles: settleSpy.mock.calls.length, publishes: publishes.length, siblingUpdates: probeUpdates.count, commits: commits.length };
	fire(range, 'change');
	await settle(4);
	return { during, commits: commits.length };
}

describe('per-change work of a slider in a form with sibling fields', () => {
	it.each([false, true])('is one settle pass, one publish and one update per sibling for one input (commit callback: %s)', async (withCommit) => {
		const { during } = await drag(withCommit, 1);

		expect(during).toEqual({ settles: 1, publishes: 1, siblingUpdates: 3, commits: 0 });
	});

	it('scales exactly with the number of inputs in a drag and commits once at its end', async () => {
		const { during, commits } = await drag(true, 6);

		expect(during).toEqual({ settles: 6, publishes: 6, siblingUpdates: 18, commits: 0 });
		expect(commits).toBe(1);
	});
});
