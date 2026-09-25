import { describe, it, expect } from 'vitest';
import type { Automation } from '$lib/types/automations';
import { applyAutomationsFilters, DEFAULT_AUTOMATIONS_FILTERS } from './automationsFilters';

function automation(overrides: Partial<Automation> = {}): Automation {
	return {
		id: 'a1',
		name: 'Tag new LoRAs',
		description: null,
		enabled: false,
		graph: { nodes: [], edges: [] },
		version: 1,
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

describe('applyAutomationsFilters', () => {
	const alpha = automation({ id: 'alpha', name: 'Alpha sync', created_at: '2026-09-01T00:00:00.000Z' });
	const beta = automation({
		id: 'beta',
		name: 'Beta cleanup',
		description: 'removes stale outputs',
		created_at: '2026-09-03T00:00:00.000Z'
	});
	const automations = [alpha, beta];

	it('matches on name', () => {
		expect(applyAutomationsFilters(automations, { ...DEFAULT_AUTOMATIONS_FILTERS, q: 'alpha' })).toEqual([alpha]);
	});

	it('matches on description', () => {
		expect(applyAutomationsFilters(automations, { ...DEFAULT_AUTOMATIONS_FILTERS, q: 'stale' })).toEqual([beta]);
	});

	it('sorts by created_at descending by default', () => {
		expect(applyAutomationsFilters(automations, DEFAULT_AUTOMATIONS_FILTERS).map((a) => a.id)).toEqual([
			'beta',
			'alpha'
		]);
	});

	it('sorts by name when requested', () => {
		expect(
			applyAutomationsFilters(automations, { ...DEFAULT_AUTOMATIONS_FILTERS, sortBy: 'name' }).map((a) => a.id)
		).toEqual(['alpha', 'beta']);
	});
});
