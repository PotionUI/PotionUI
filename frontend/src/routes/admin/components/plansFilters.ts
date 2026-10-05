import type { Plan } from '$lib/plans/types';

export interface PlansFilters {
	q: string;
}

export const DEFAULT_PLANS_FILTERS: PlansFilters = { q: '' };

const PLAN_Q_PARAM = 'plan_q';

export function plansFiltersFromSearchParams(params: URLSearchParams): PlansFilters {
	return { q: params.get(PLAN_Q_PARAM) ?? '' };
}

export function plansFiltersToSearchParams(filters: PlansFilters): URLSearchParams {
	const out = new URLSearchParams();
	if (filters.q) out.set(PLAN_Q_PARAM, filters.q);
	return out;
}

export function applyPlansFilters(plans: readonly Plan[], filters: PlansFilters): Plan[] {
	const query = filters.q.trim().toLowerCase();
	return plans.filter(
		(plan) => !query || plan.name.toLowerCase().includes(query) || (plan.description ?? '').toLowerCase().includes(query)
	);
}
