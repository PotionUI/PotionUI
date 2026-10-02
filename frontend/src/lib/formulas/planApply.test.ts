import { describe, expect, it } from 'vitest';
import { applyPlan, diffLoraRows, loraRowsOf, orderControllersFirst, planApply, undoApplied } from './planApply';
import type { PlanChange, ServerPlan } from './types';

function change(field: string, oldValue: unknown, newValue: unknown, extra: Partial<PlanChange> = {}): PlanChange {
	return { field, label: field, group: 'speed', groupLabel: 'Speed', old: oldValue, new: newValue, advanced: false, ...extra };
}

function server(changes: PlanChange[], skips: ServerPlan['skips'] = [], same: ServerPlan['same'] = []): ServerPlan {
	return { changes, same, skips };
}

describe('planApply', () => {
	it('keeps changes, same and skips from the server plan', () => {
		const plan = planApply(
			server(
				[change('speed_profile', 'quality', 'turbo')],
				[{ field: 'x', label: 'X', group: 'speed', code: 'option_missing', reason: 'gone' }],
				[{ field: 'size', label: 'Size', group: 'size' }]
			)
		);
		expect(plan.changes.map((c) => c.field)).toEqual(['speed_profile']);
		expect(plan.same).toHaveLength(1);
		expect(plan.skips).toHaveLength(1);
	});

	it('puts controllers before the fields they pin', () => {
		const plan = planApply(
			server([change('steps', 24, 4), change('sampler', 'euler', 'res'), change('speed_profile', 'quality', 'turbo')]),
			{ dependencies: { steps: ['speed_profile'], sampler: ['speed_profile'] } }
		);
		expect(plan.changes.map((c) => c.field)).toEqual(['speed_profile', 'steps', 'sampler']);
	});

	it('survives a dependency cycle without looping', () => {
		const ordered = orderControllersFirst([{ field: 'a' }, { field: 'b' }], { a: ['b'], b: ['a'] });
		expect(ordered.map((i) => i.field).sort()).toEqual(['a', 'b']);
	});

	it('moves a field the selected cloud model rejects to skipped', () => {
		const plan = planApply(server([change('aspect', '1:1', '9:21'), change('steps', 20, 30)]), {
			capabilityInvalid: () => ({ aspect: [] })
		});
		expect(plan.changes.map((c) => c.field)).toEqual(['steps']);
		expect(plan.skips).toHaveLength(1);
		expect(plan.skips[0]).toMatchObject({ field: 'aspect', reason: 'Not valid for the selected model' });
	});

	it('drops only the invalid keys of a cloud options object', () => {
		const plan = planApply(server([change('cloud', {}, { quality: 'hd', style: 'vivid' })]), {
			capabilityInvalid: () => ({ cloud: ['style'] })
		});
		expect(plan.changes[0].new).toEqual({ quality: 'hd' });
		expect(plan.skips.map((s) => s.row)).toEqual(['style']);
	});

	it('skips a cloud options object when every key is invalid', () => {
		const plan = planApply(server([change('cloud', {}, { style: 'vivid' })]), {
			capabilityInvalid: () => ({ cloud: ['style'] })
		});
		expect(plan.changes).toHaveLength(0);
		expect(plan.skips).toHaveLength(1);
	});
});

describe('applyPlan and undo', () => {
	const base = { prompt: 'a cat', seed: 7, steps: 24, speed_profile: 'quality', extra: { nested: [1, 2] } };

	it('patches only the selected fields and nothing else', () => {
		const plan = planApply(server([change('steps', 24, 4), change('speed_profile', 'quality', 'turbo')]));
		const result = applyPlan(base, plan, new Set(['speed_profile']));
		expect(result.formData).toEqual({ ...base, speed_profile: 'turbo' });
		expect(result.formData.steps).toBe(24);
		expect(Object.keys(result.changed)).toEqual(['speed_profile']);
	});

	it('never writes outside the planned fields', () => {
		const plan = planApply(server([change('steps', 24, 4)]));
		const result = applyPlan(base, plan, new Set(['steps', 'prompt']));
		expect(result.formData.prompt).toBe('a cat');
		expect(result.formData.seed).toBe(7);
	});

	it('does not mutate the original form data', () => {
		const before = JSON.stringify(base);
		applyPlan(base, planApply(server([change('steps', 24, 4)])), new Set(['steps']));
		expect(JSON.stringify(base)).toBe(before);
	});

	it('restores the form byte for byte on undo', () => {
		const plan = planApply(server([change('steps', 24, 4), change('fresh', undefined, 'x')]));
		const result = applyPlan(base, plan, new Set(['steps', 'fresh']));
		expect(result.formData).not.toEqual(base);
		const restored = undoApplied(result.formData, result.snapshot);
		expect(JSON.stringify(restored)).toBe(JSON.stringify(base));
		expect('fresh' in restored).toBe(false);
	});

	it('undo keeps edits the user made to untouched fields', () => {
		const result = applyPlan(base, planApply(server([change('steps', 24, 4)])), new Set(['steps']));
		const restored = undoApplied({ ...result.formData, prompt: 'a dog' }, result.snapshot);
		expect(restored.prompt).toBe('a dog');
		expect(restored.steps).toBe(24);
	});

	it('carries companion values with their field and keeps them out of the change list', () => {
		const plan = planApply(
			server([change('model', 'a', 'b'), change('model_tagFilters', ['x'], ['y'], { companionOf: 'model' })])
		);
		const result = applyPlan({ model: 'a', model_tagFilters: ['x'] }, plan, new Set(['model']));
		expect(result.formData).toEqual({ model: 'b', model_tagFilters: ['y'] });
		expect(Object.keys(result.changed)).toEqual(['model']);
		expect(undoApplied(result.formData, result.snapshot)).toEqual({ model: 'a', model_tagFilters: ['x'] });
	});

	it('does not apply a companion whose owner the capability check skipped', () => {
		const plan = planApply(
			server([change('model', 'a', 'b'), change('model_tagFilters', ['x'], ['y'], { companionOf: 'model' })]),
			{ capabilityInvalid: () => ({ model: [] }) }
		);
		const selected = new Set(plan.changes.filter((c) => !c.companionOf).map((c) => c.field));
		const result = applyPlan({ model: 'a', model_tagFilters: ['x'] }, plan, selected);
		expect(plan.skips.map((s) => s.field)).toEqual(['model']);
		expect(result.formData).toEqual({ model: 'a', model_tagFilters: ['x'] });
		expect(result.snapshot).toEqual({ values: {}, absent: [] });
	});

	it('undo after a second apply goes back one step, not to the original', () => {
		const first = applyPlan(base, planApply(server([change('steps', 24, 4)])), new Set(['steps']));
		const second = applyPlan(first.formData, planApply(server([change('steps', 4, 8)])), new Set(['steps']));
		expect(undoApplied(second.formData, second.snapshot).steps).toBe(4);
		expect(undoApplied(second.formData, first.snapshot).steps).toBe(24);
	});

	it('applying the same plan twice leaves the same form', () => {
		const plan = planApply(server([change('steps', 24, 4)]));
		const once = applyPlan(base, plan, new Set(['steps'])).formData;
		const twice = applyPlan(once, plan, new Set(['steps'])).formData;
		expect(twice).toEqual(once);
	});
});

describe('lora rows', () => {
	const a = { model: 'model:a', strength: 0.8 };
	const b = { model: 'model:b', strength: 0.75 };

	it('marks added, removed, changed and unchanged rows', () => {
		const rows = diffLoraRows([a, { model: 'model:c', strength: 1 }], [{ ...a, strength: 0.5 }, b, { model: 'model:c', strength: 1 }]);
		const byKey = Object.fromEntries(rows.map((row) => [row.key, row.status]));
		expect(byKey).toEqual({ 'model:a': 'changed', 'model:b': 'added', 'model:c': 'same' });
	});

	it('reports a replaced list as removals plus additions', () => {
		const rows = diffLoraRows([a], [b]);
		expect(rows.map((row) => row.status)).toEqual(['removed', 'added']);
	});

	it('uses the rows the server computed when present', () => {
		const rows = loraRowsOf(
			change('loras', [a], [b], {
				rows: [
					{ model: 'model:b', status: 'added', old: null, new: b },
					{ model: 'model:a', status: 'removed', old: a, new: null }
				]
			})
		);
		expect(rows).toEqual([
			{ key: 'model:b', name: 'b', status: 'added', oldStrength: null, newStrength: 0.75 },
			{ key: 'model:a', name: 'a', status: 'removed', oldStrength: 0.8, newStrength: null }
		]);
	});
});
