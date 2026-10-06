import { describe, expect, it } from 'vitest';
import { applyDiff, failureSummary } from './applyDiff';

describe('applyDiff', () => {
	it('unassigns removals and assigns additions, reporting everything ok', async () => {
		const calls: string[] = [];
		const result = await applyDiff(
			{ add: ['a', 'b'], remove: ['x'] },
			{
				assign: async (id) => void calls.push(`+${id}`),
				unassign: async (id) => void calls.push(`-${id}`)
			}
		);
		expect(calls).toEqual(['-x', '+a', '+b']);
		expect(result).toEqual({ ok: ['x', 'a', 'b'], failed: [] });
	});

	it('reports a partial failure per id without stopping the rest', async () => {
		const result = await applyDiff(
			{ add: ['a', 'b', 'c'], remove: [] },
			{
				assign: async (id) => {
					if (id === 'b') throw new Error('Preset is not installed');
				},
				unassign: async () => {}
			},
			2
		);
		expect(result.ok).toEqual(['a', 'c']);
		expect(result.failed).toEqual([{ id: 'b', message: 'Preset is not installed' }]);
		expect(failureSummary(result, 'presets')).toBe('1 of 3 changes failed. The other presets changes were applied.');
	});

	it('does nothing for an empty diff', async () => {
		const result = await applyDiff({ add: [], remove: [] }, { assign: async () => {}, unassign: async () => {} });
		expect(result).toEqual({ ok: [], failed: [] });
		expect(failureSummary(result, 'presets')).toBe('');
	});
});
