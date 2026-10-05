import { formatLimitChip } from './format';
import type { LimitKindDescriptor, Plan } from './types';

export const INHERIT_PLAN = '__inherit__';

export interface PlanOption {
	value: string;
	label: string;
	description?: string;
}

export function planSummary(plan: Plan, kinds: readonly LimitKindDescriptor[]): string {
	const parts = plan.limits.flatMap((limit) => {
		const kind = kinds.find((k) => k.key === limit.kind);
		return kind ? [formatLimitChip(kind, limit.value)] : [];
	});
	return parts.length ? parts.join(' - ') : 'no limits';
}

export function planOptions(
	plans: readonly Plan[],
	kinds: readonly LimitKindDescriptor[],
	opts: { inheritLabel: string }
): PlanOption[] {
	return [
		{ value: INHERIT_PLAN, label: opts.inheritLabel },
		...plans.map((plan) => ({ value: plan.id, label: plan.name, description: planSummary(plan, kinds) }))
	];
}
