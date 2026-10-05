import { describe, it, expect } from 'vitest';
import { applyPlansFilters, plansFiltersFromSearchParams, plansFiltersToSearchParams } from './plansFilters';
import type { Plan } from '$lib/plans/types';

const plan = (name: string, description = ''): Plan => ({ id: name, name, description, limits: [], is_system: false });

describe('plansFilters', () => {
	const plans = [plan('Free'), plan('Tier 1', 'first paid tier'), plan('Tier 2')];

	it('matches name and description case-insensitively', () => {
		expect(applyPlansFilters(plans, { q: 'tier' }).map((p) => p.name)).toEqual(['Tier 1', 'Tier 2']);
		expect(applyPlansFilters(plans, { q: 'PAID' }).map((p) => p.name)).toEqual(['Tier 1']);
		expect(applyPlansFilters(plans, { q: '' })).toHaveLength(3);
	});

	it('round-trips the query through the url', () => {
		const params = plansFiltersToSearchParams({ q: 'tier' });
		expect(params.toString()).toBe('plan_q=tier');
		expect(plansFiltersFromSearchParams(params)).toEqual({ q: 'tier' });
		expect(plansFiltersToSearchParams({ q: '' }).toString()).toBe('');
	});
});
