// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	api: new Proxy({}, { get: () => () => undefined })
}));

const { default: EntryForm } = await import('$lib/components/tool-tabs/frame/EntryForm.svelte');
const { toolParamsToSchema } = await import('$lib/media-tools/toolSchema');

const PARAMS = [
	{ name: 'amount', type: 'float', label: 'Amount', min: 0, max: 100, default: 30 },
	{ name: 'mode', type: 'select', label: 'Mode', options: ['a', 'b'], default: 'a' },
	{ name: 'extra', type: 'float', label: 'Extra', min: 0, max: 10, default: 1, when: { field: 'mode', equals: 'b' } }
] as never;

type Info = { commit: boolean; name?: string };
let calls: Array<{ values: Record<string, unknown>; info: Info }>;
let view: ReturnType<typeof mountComponent>;

async function open(values: Record<string, unknown> = { amount: 30, mode: 'a' }, extra: Record<string, unknown> = {}) {
	calls = [];
	view = mountComponent(EntryForm as never, {
		fields: toolParamsToSchema(PARAMS),
		values,
		onValues: (next: Record<string, unknown>, info: Info) => calls.push({ values: next, info }),
		...extra
	});
	await settle(10);
}

const range = () => document.body.querySelector<HTMLInputElement>('[data-field-name="amount"] input[type="range"]')!;
const drag = (value: number) => {
	range().value = String(value);
	range().dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
};

beforeEach(() => {
	vi.useRealTimers();
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('EntryForm', () => {
	it('draws the schema with the values it is given and reports nothing for the first paint', async () => {
		await open({ amount: 55, mode: 'a' });
		expect(range().value).toBe('55');
		expect(document.body.querySelector('[data-field-name="extra"]')).toBeNull();
		expect(document.body.textContent).not.toContain('tick = default');
		expect(calls).toEqual([]);
	});

	it('reports a change as it happens, with the whole values and only the changed key touched', async () => {
		await open({ amount: 30, mode: 'a', untouched: 'kept' });
		drag(42);
		expect(calls).toHaveLength(1);
		expect(calls[0].values).toEqual({ amount: 42, mode: 'a', untouched: 'kept' });
		expect(calls[0].info).toEqual({ commit: false, name: 'amount' });
	});

	it('reports every step of a drag at once, with no timer between the input and the report', async () => {
		await open();
		vi.useFakeTimers();
		drag(41);
		drag(43);
		drag(47);
		expect(calls.map((call) => call.values.amount)).toEqual([41, 43, 47]);
		expect(vi.getTimerCount()).toBeLessThanOrEqual(1);
	});

	it('reports one commit when the drag is released, not one per step', async () => {
		await open();
		drag(41);
		drag(43);
		range().dispatchEvent(new Event('change', { bubbles: true }));
		await settle(10);
		const commits = calls.filter((call) => call.info.commit);
		expect(commits).toHaveLength(1);
		expect(commits[0].info.name).toBe('amount');
		expect(commits[0].values.amount).toBe(43);
	});

	it('shows values written from outside without reporting them back', async () => {
		await open();
		view.rerender({ values: { amount: 77, mode: 'a' } });
		await settle(10);
		expect(range().value).toBe('77');
		expect(calls).toEqual([]);
		drag(80);
		expect(calls[calls.length - 1].values.amount).toBe(80);
	});

	it('does not echo the values it reported when the parent hands them back', async () => {
		await open();
		drag(60);
		view.rerender({ values: calls[0].values });
		await settle(10);
		expect(calls).toHaveLength(1);
		expect(range().value).toBe('60');
	});

	it('reveals a conditional field when its controlling choice changes', async () => {
		await open();
		view.rerender({ values: { amount: 30, mode: 'b' } });
		await settle(10);
		expect(document.body.querySelector('[data-field-name="extra"]')).not.toBeNull();
	});

	it('keeps the user input when the schema stays the same and starts over when it changes', async () => {
		await open({ amount: 30, mode: 'a' });
		drag(66);
		view.rerender({ fields: toolParamsToSchema(PARAMS), values: calls[0].values });
		await settle(10);
		expect(range().value).toBe('66');
		view.rerender({ fields: toolParamsToSchema([{ name: 'other', type: 'float', label: 'Other', min: 0, max: 5, default: 2 }] as never), values: { other: 4 } });
		await settle(10);
		expect(document.body.querySelector('[data-field-name="amount"]')).toBeNull();
		expect(document.body.querySelector<HTMLInputElement>('[data-field-name="other"] input[type="range"]')!.value).toBe('4');
	});

	it('shows a message under the field it names and marks the control invalid', async () => {
		await open({ amount: 30, mode: 'a' }, { errors: { amount: 'At most 20' } });
		const field = document.body.querySelector<HTMLElement>('[data-field-name="amount"]')!;
		expect(field.querySelector('[data-field-error]')?.textContent).toContain('At most 20');
		expect(range().getAttribute('aria-invalid')).toBe('true');
	});

	it('resets a slider to its default with a double click', async () => {
		await open({ amount: 80, mode: 'a' });
		const field = document.body.querySelector<HTMLElement>('[data-field-name="amount"] [role="presentation"]') ?? range().parentElement!;
		range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		await settle(10);
		void field;
		expect(calls[calls.length - 1].values.amount).toBe(30);
	});
});
