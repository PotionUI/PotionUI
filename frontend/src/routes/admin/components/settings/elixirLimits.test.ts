import { describe, expect, it } from 'vitest';
import { ELIXIR_LIMITS, limitValue, parseLimit } from './elixirLimits';
import { SETTINGS_KEY_GROUP } from './settingsGroups';

describe('elixir limits', () => {
	it('saves every limit with the Generation group', () => {
		expect(ELIXIR_LIMITS.map((limit) => limit.key)).toEqual([
			'elixirs_max_nodes',
			'elixirs_max_edges',
			'elixirs_max_branches',
			'elixirs_max_media_per_source',
			'elixirs_max_outputs_per_connection',
			'elixirs_max_invocations'
		]);
		for (const limit of ELIXIR_LIMITS) expect(SETTINGS_KEY_GROUP[limit.key]).toBe('generation');
	});

	it('shows the stored value or the default', () => {
		expect(limitValue(30, 24)).toBe(30);
		expect(limitValue('12', 24)).toBe(12);
		expect(limitValue(undefined, 24)).toBe(24);
		expect(limitValue(0, 24)).toBe(24);
	});

	it('accepts whole numbers of one or more', () => {
		expect(parseLimit('7.4')).toBe(7);
		expect(parseLimit('0')).toBeNull();
		expect(parseLimit('abc')).toBeNull();
	});
});
