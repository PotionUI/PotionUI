import { describe, expect, it } from 'vitest';
import { normalizePlan } from './normalize';

describe('normalizePlan', () => {
	it('maps the server plan to the names the drawer uses', () => {
		const plan = normalizePlan({
			changes: [
				{ name: 'steps', label: 'Steps', group_id: 'speed', group_label: 'Speed', type: 'slider', advanced: true, old: 24, new: 4 },
				{ name: 'loras', label: 'LoRAs', group_id: 'loras', group_label: 'LoRAs', old: [], new: [], rows: [{ model: 'model:a', status: 'added', old: null, new: {} }] },
				{ name: 'steps_tagFilters', label: 'Steps', group_id: 'speed', group_label: 'Speed', companion_of: 'steps', old: null, new: [] }
			],
			same: [{ name: 'size', label: 'Size', group_id: 'size' }],
			skips: [
				{ name: 'x', label: 'X', group_id: 'speed', code: 'lora_unavailable', reason: 'Missing', detail: { model: 'model:z' } },
				{ name: 'y', label: 'Y', group_id: 'speed', code: 'option_missing', reason: 'Gone', detail: {} }
			]
		});
		expect(plan.changes[0]).toMatchObject({ field: 'steps', group: 'speed', groupLabel: 'Speed', advanced: true, type: 'slider' });
		expect(plan.changes[1].rows).toHaveLength(1);
		expect(plan.changes[2].companionOf).toBe('steps');
		expect(plan.same).toEqual([{ field: 'size', label: 'Size', group: 'size' }]);
		expect(plan.skips[0]).toMatchObject({ field: 'x', row: 'model:z', library: true });
		expect(plan.skips[1].library).toBe(false);
	});

	it('returns an empty plan for an empty response', () => {
		expect(normalizePlan(null)).toEqual({ changes: [], same: [], skips: [] });
	});
});
