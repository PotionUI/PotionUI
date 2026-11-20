import { beforeEach, describe, expect, it } from 'vitest';
import { writable } from 'svelte/store';
import type { EntryContext } from '$lib/editors/railTools';
import { filterCatalog } from '$lib/filters/catalog';
import { OPS_BY_ID } from '$lib/filters/engine';
import { commitLive, deserialize, serialize, undo, appliedSteps, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { setAdjustValue } from './adjustSections';
import { GRADE_ENTRIES } from './entries';
import { gradeEntry, gradeFieldName, opSlidersEntry } from './entrySchemas';
import { CURVE_POINTS } from './steps';
import type { ImageToolHost } from './toolHost';
import oldPanels from './fixtures/oldPanels.json';

type Old = { kind: string; entry: string; key?: string; param?: string; value?: number; startOp?: string; startParam?: string; startValue?: number; live: string; committed: string };
const fixtures = oldPanels as Old[];

const empty = (): EditStackState<unknown> => deserialize({ v: 1, steps: [], cursor: 0, pending: null, seq: 0 });
const dump = (stack: EditStackState<unknown>) => JSON.stringify(serialize(stack));

function fakeHost(initial: EditStackState<unknown>) {
	let stack = initial;
	const calls = { edit: [] as Array<{ hold?: boolean }>, settle: 0 };
	const host = {
		current: () => stack,
		edit: (next: EditStackState<unknown>, options?: { hold?: boolean }) => {
			stack = next;
			calls.edit.push(options ?? {});
		},
		settle: () => {
			calls.settle += 1;
			stack = commitLive(stack);
		},
		pending: { subscribe: writable(null).subscribe },
		stack: { subscribe: writable(stack).subscribe }
	};
	return { host: host as unknown as ImageToolHost, calls, stack: () => stack };
}

function ctxOf(fake: ReturnType<typeof fakeHost>, tool: string): EntryContext {
	return { host: fake.host, phone: false, tool, pending: null, stack: fake.stack(), catalog: [] };
}

type Body = ReturnType<typeof gradeEntry>;
const shown = (entry: Body, fake: ReturnType<typeof fakeHost>, tool: string) => entry.values!(ctxOf(fake, tool));
const write = (entry: Body, fake: ReturnType<typeof fakeHost>, tool: string, values: Record<string, unknown>, commit = false) => entry.onValues!(values, { commit }, ctxOf(fake, tool));

beforeEach(() => {
	filterCatalog.set({ status: 'ready', items: [], ops: Object.values(OPS_BY_ID), groups: [], error: null });
});

describe.each(['light', 'colour'] as const)('%s entry against the old panel', (id) => {
	const entry = gradeEntry(id);
	const grade = GRADE_ENTRIES[id];

	it('declares one signed, resettable slider per control with its old label, in the old order', () => {
		const fake = fakeHost(empty());
		const fields = (entry.schema as unknown as (c: EntryContext) => Array<Record<string, unknown>>)(ctxOf(fake, id));
		expect(fields.map((field) => field.title)).toEqual(grade.controls.map((control) => control.label));
		expect(fields.map((field) => field.name)).toEqual(grade.controls.map(gradeFieldName));
		for (const field of fields) {
			expect(field.type).toBe('slider');
			expect(field.reset).toBe(true);
			expect(field.legend).toBe(false);
			expect(field.signed).toBe((field.minimum as number) < 0 ? true : undefined);
			if ((field.minimum as number) < 0) expect(field.fill).toBe('from-default');
		}
	});

	it('writes the same step as the old slider for every control and value, held until the commit', () => {
		const cases = fixtures.filter((item) => item.kind === 'grade' && item.entry === id);
		expect(cases.length).toBeGreaterThan(0);
		for (const old of cases) {
			const fake = fakeHost(empty());
			const name = old.key!.replace('.', '_');
			write(entry, fake, id, { ...shown(entry, fake, id), [name]: old.value });
			expect(dump(fake.stack()), `${old.key}=${old.value}`).toBe(old.live);
			expect(fake.calls.settle).toBe(0);
			write(entry, fake, id, shown(entry, fake, id), true);
			expect(dump(fake.stack()), `${old.key}=${old.value} committed`).toBe(old.committed);
		}
	});

	it('writes the same step for a run of edits one control after another', () => {
		const old = fixtures.find((item) => item.kind === 'grade-chain' && item.entry === id)!;
		const fake = fakeHost(empty());
		grade.controls.forEach((control, index) => write(entry, fake, id, { ...shown(entry, fake, id), [gradeFieldName(control)]: index + 3 }));
		expect(dump(fake.stack())).toBe(old.live);
		write(entry, fake, id, shown(entry, fake, id), true);
		expect(dump(fake.stack())).toBe(old.committed);
	});

	it('keeps an edit saved with the old per-op steps in that shape', () => {
		const cases = fixtures.filter((item) => item.kind === 'grade-legacy' && item.entry === id);
		expect(cases.length).toBeGreaterThan(0);
		for (const old of cases) {
			const start = commitLive(setAdjustValue(empty(), OPS_BY_ID[old.startOp!], old.startParam!, old.startValue!));
			const fake = fakeHost(start);
			write(entry, fake, id, { ...shown(entry, fake, id), [old.key!.replace('.', '_')]: old.value });
			expect(dump(fake.stack()), old.key).toBe(old.live);
			write(entry, fake, id, shown(entry, fake, id), true);
			expect(dump(fake.stack()), `${old.key} committed`).toBe(old.committed);
		}
	});

	it('reads the stored values back and writes nothing when nothing moved', () => {
		const fake = fakeHost(empty());
		const control = grade.controls[1];
		write(entry, fake, id, { ...shown(entry, fake, id), [gradeFieldName(control)]: 12 });
		write(entry, fake, id, shown(entry, fake, id), true);
		expect(shown(entry, fake, id)[gradeFieldName(control)]).toBe(12);
		const editsBefore = fake.calls.edit.length;
		write(entry, fake, id, shown(entry, fake, id));
		expect(fake.calls.edit.length).toBe(editsBefore);
	});

	it('is one undo step for a whole drag and the commit signal settles it', () => {
		const fake = fakeHost(empty());
		const control = grade.controls[0];
		const name = gradeFieldName(control);
		for (const value of [0.1, 0.2, 0.3, 0.4]) write(entry, fake, id, { ...shown(entry, fake, id), [name]: value });
		expect(fake.calls.edit.every((options) => options.hold === true)).toBe(true);
		write(entry, fake, id, shown(entry, fake, id), true);
		expect(fake.calls.settle).toBe(1);
		expect(appliedSteps(fake.stack())).toHaveLength(1);
		expect(appliedSteps(undo(fake.stack()))).toHaveLength(0);
	});
});

describe.each([
	['split_tone', 'split_tone'],
	['curves', 'curves']
] as const)('%s entry against the old panel', (id, op) => {
	const entry = opSlidersEntry(id);
	const names = id === 'curves' ? CURVE_POINTS.map((point) => point.id as string) : ['shadow_hue', 'shadow_sat', 'highlight_hue', 'highlight_sat', 'balance'];

	it('declares the old sliders in the old order', () => {
		const fields = (entry.schema as unknown as (c: EntryContext) => Array<Record<string, unknown>>)(ctxOf(fakeHost(empty()), id));
		expect(fields.map((field) => field.name)).toEqual(names);
		expect(fields.every((field) => field.type === 'slider' && field.reset === true)).toBe(true);
	});

	it('writes the same step as the old slider for every param and value', () => {
		const cases = fixtures.filter((item) => item.kind === 'op' && item.entry === id);
		expect(cases.length).toBeGreaterThan(0);
		for (const old of cases) {
			const fake = fakeHost(empty());
			write(entry, fake, id, { ...shown(entry, fake, id), [old.param!]: old.value });
			expect(dump(fake.stack()), `${old.param}=${old.value}`).toBe(old.live);
			write(entry, fake, id, shown(entry, fake, id), true);
			expect(dump(fake.stack()), `${old.param}=${old.value} committed`).toBe(old.committed);
		}
	});

	it('reads back what was stored and keeps the op id on the step', () => {
		const fake = fakeHost(empty());
		write(entry, fake, id, { ...shown(entry, fake, id), [names[0]]: 25 });
		write(entry, fake, id, shown(entry, fake, id), true);
		expect(shown(entry, fake, id)[names[0]]).toBe(25);
		expect((appliedSteps(fake.stack())[0].params as { op: string }).op).toBe(op);
	});
});

describe('curves chain', () => {
	it('matches the old run of three point edits', () => {
		const old = fixtures.find((item) => item.kind === 'op-chain')!;
		const entry = opSlidersEntry('curves');
		const fake = fakeHost(empty());
		CURVE_POINTS.forEach((point, index) => write(entry, fake, 'curves', { ...shown(entry, fake, 'curves'), [point.id]: index * 10 - 20 }));
		expect(dump(fake.stack())).toBe(old.live);
		write(entry, fake, 'curves', shown(entry, fake, 'curves'), true);
		expect(dump(fake.stack())).toBe(old.committed);
	});
});


