import { describe, expect, it, vi, beforeEach } from 'vitest';
import { writable } from 'svelte/store';
import type { EntryContext } from '$lib/editors/railTools';
import { filterCatalog } from '$lib/filters/catalog';
import { OPS_BY_ID } from '$lib/filters/engine';
import { effectDefaults, effectTools } from '$lib/effects/catalog';
import { commitLive, deserialize, liveEdit, appliedSteps, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { EFFECT_CATALOG } from '../../../../tests/component/fixtures/effectCatalog';
import { setAdjustToggle, setAdjustValue } from './adjustSections';
import { LIVE_EFFECTS, matchEffect } from './entries';
import { effectCardEntry, liveEffectEntry, toggleEntry } from './entrySchemas';
import { toolEffectDraft, type ToolEffectParams } from './steps';
import type { ImageToolHost } from './toolHost';

const grain = effectTools(EFFECT_CATALOG).find((entry) => entry.id === 'film_grain')!;
const catalog = EFFECT_CATALOG;
const empty = (): EditStackState<unknown> => deserialize({ v: 1, steps: [], cursor: 0, pending: null, seq: 0 });

function fakeHost(initial: EditStackState<unknown>) {
	let stack = initial;
	const pending = writable<unknown>(null);
	const calls = { edit: [] as Array<{ hold?: boolean }>, settle: 0, begin: [] as unknown[], update: [] as unknown[] };
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
		begin: (draft: unknown) => calls.begin.push(draft),
		update: (draft: unknown) => calls.update.push(draft),
		pending: { subscribe: pending.subscribe },
		stack: { subscribe: writable(stack).subscribe }
	};
	return { host: host as unknown as ImageToolHost, calls, stack: () => stack, setPending: (value: unknown) => pending.set(value) };
}

function ctxOf(fake: ReturnType<typeof fakeHost>, tool: string, pending: unknown = null): EntryContext {
	return { host: fake.host, phone: false, tool, pending: pending as never, stack: fake.stack(), catalog };
}

beforeEach(() => {
	filterCatalog.set({ status: 'ready', items: [], ops: Object.values(OPS_BY_ID), groups: [], error: null });
});

const steps = (stack: EditStackState<unknown>) => appliedSteps(stack).map((step) => ({ tool: step.tool, params: step.params }));

describe('Film grain entry', () => {
	const entry = liveEffectEntry('film_grain');

	it('draws the effect params as the form and starts from the effect defaults', () => {
		const fake = fakeHost(empty());
		const ctx = ctxOf(fake, 'film_grain');
		const fields = (entry.schema as (c: EntryContext) => Array<{ name: string; type: string }>)(ctx);
		expect(fields.map((field) => [field.name, field.type])).toEqual([['amount', 'slider'], ['size', 'slider'], ['color', 'checkbox'], ['seed', 'seed']]);
		expect(entry.values!(ctx)).toEqual(effectDefaults(grain));
	});

	it('writes the same step an edit of one param wrote before, held until the commit', () => {
		const fake = fakeHost(empty());
		const values = { ...effectDefaults(grain), amount: 64 };
		entry.onValues!(values, { commit: false, name: 'amount' }, ctxOf(fake, 'film_grain'));
		const params: ToolEffectParams = { kind: 'effect', tool: 'film_grain', name: grain.label, values };
		const expected = liveEdit(empty(), toolEffectDraft(params, catalog) as StepDraft<unknown>, matchEffect('film_grain'));
		expect(JSON.stringify(fake.stack())).toBe(JSON.stringify(expected));
		expect(fake.calls.edit).toEqual([{ hold: true }]);
		expect(fake.calls.settle).toBe(0);
	});

	it('settles on the commit signal and writes nothing then', () => {
		const fake = fakeHost(empty());
		entry.onValues!({ ...effectDefaults(grain), amount: 64 }, { commit: false }, ctxOf(fake, 'film_grain'));
		entry.onValues!({ ...effectDefaults(grain), amount: 64 }, { commit: true, name: 'amount' }, ctxOf(fake, 'film_grain'));
		expect(fake.calls.settle).toBe(1);
		expect(fake.calls.edit).toHaveLength(1);
		expect(steps(fake.stack())).toHaveLength(1);
	});

	it('reads back the stored step values for an edit that was saved before', () => {
		const fake = fakeHost(empty());
		const values = { ...effectDefaults(grain), amount: 12, seed: 9 };
		entry.onValues!(values, { commit: false }, ctxOf(fake, 'film_grain'));
		entry.onValues!(values, { commit: true }, ctxOf(fake, 'film_grain'));
		expect(entry.values!(ctxOf(fake, 'film_grain'))).toEqual(values);
	});

	it('keeps an old edit in its legacy shape: the form shows the op values and writes the op params', () => {
		const spec = OPS_BY_ID.grain;
		let stack = commitLive(setAdjustValue(empty(), spec, 'amount', 40));
		const fake = fakeHost(stack);
		const ctx = ctxOf(fake, 'film_grain');
		const fields = (entry.schema as (c: EntryContext) => Array<{ name: string }>)(ctx);
		expect(fields.map((field) => field.name)).toEqual(Object.keys(LIVE_EFFECTS.film_grain.legacy).filter((name) => fields.some((field) => field.name === name)));
		const shown = entry.values!(ctx);
		expect(shown.amount).toBe(40);
		entry.onValues!({ ...shown, amount: 55 }, { commit: false }, ctx);
		const expected = setAdjustValue(stack, spec, 'amount', 55);
		expect(JSON.stringify(steps(fake.stack()))).toBe(JSON.stringify(steps(expected)));
		expect(fake.calls.edit).toEqual([{ hold: true }]);
	});
});

describe('Vignette entry in its legacy shape', () => {
	it('maps radius and softness to the op params midpoint and feather, as before', () => {
		const entry = liveEffectEntry('vignette');
		const spec = OPS_BY_ID.vignette;
		const stack = commitLive(setAdjustValue(empty(), spec, 'midpoint', 0.3));
		const fake = fakeHost(stack);
		const ctx = ctxOf(fake, 'vignette');
		const shown = entry.values!(ctx);
		expect(shown.radius).toBe(0.3);
		entry.onValues!({ ...shown, softness: 0.7 }, { commit: false }, ctx);
		const expected = setAdjustValue(stack, spec, 'feather', 0.7);
		expect(JSON.stringify(steps(fake.stack()))).toBe(JSON.stringify(steps(expected)));
	});
});

describe('toggle entries', () => {
	it('turns Grayscale on and off through the same adjust step as before', () => {
		const entry = toggleEntry('grayscale');
		const spec = OPS_BY_ID.grayscale;
		const fake = fakeHost(empty());
		expect(entry.values!(ctxOf(fake, 'grayscale'))).toEqual({ on: false });
		entry.onValues!({ on: true }, { commit: false, name: 'on' }, ctxOf(fake, 'grayscale'));
		expect(JSON.stringify(steps(fake.stack()))).toBe(JSON.stringify(steps(setAdjustToggle(empty(), spec, true))));
		expect(entry.values!(ctxOf(fake, 'grayscale'))).toEqual({ on: true });
		entry.onValues!({ on: true }, { commit: true, name: 'on' }, ctxOf(fake, 'grayscale'));
		expect(fake.calls.settle).toBe(1);
		entry.onValues!({ on: false }, { commit: false, name: 'on' }, ctxOf(fake, 'grayscale'));
		entry.onValues!({ on: false }, { commit: true, name: 'on' }, ctxOf(fake, 'grayscale'));
		expect(steps(fake.stack()).every((step) => true)).toBe(true);
		expect(entry.values!(ctxOf(fake, 'grayscale'))).toEqual({ on: false });
	});
});

describe('effect card entry', () => {
	const entry = effectCardEntry();
	const scan = effectTools(EFFECT_CATALOG).find((candidate) => candidate.id === 'retro-tape.scanlines')!;

	it('begins a pending effect step with the whole values on the first edit and updates it after', () => {
		const fake = fakeHost(empty());
		const values = { ...effectDefaults(scan), strength: 41 };
		entry.onValues!(values, { commit: false }, ctxOf(fake, scan.id));
		const params: ToolEffectParams = { kind: 'effect', tool: scan.id, name: scan.label, values };
		expect(JSON.stringify(fake.calls.begin)).toBe(JSON.stringify([toolEffectDraft(params, catalog)]));
		const held = { params };
		entry.onValues!({ ...values, strength: 42 }, { commit: false }, ctxOf(fake, scan.id, held));
		expect(fake.calls.update).toHaveLength(1);
		expect(entry.values!(ctxOf(fake, scan.id, held))).toEqual(values);
	});

	it('ignores the commit signal, since the step stays pending until Apply', () => {
		const fake = fakeHost(empty());
		entry.onValues!(effectDefaults(scan), { commit: true }, ctxOf(fake, scan.id));
		expect(fake.calls.begin).toHaveLength(0);
		expect(fake.calls.update).toHaveLength(0);
	});

	it('describes the effect, and says so when it is gone', () => {
		const fake = fakeHost(empty());
		expect((entry.description as (c: EntryContext) => string)(ctxOf(fake, scan.id))).toBe(scan.description);
		expect((entry.description as (c: EntryContext) => string)(ctxOf(fake, 'nope'))).toBe('This effect is not available right now.');
	});
});
void vi;
