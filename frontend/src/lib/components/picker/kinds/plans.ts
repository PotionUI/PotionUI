import { formatLimitChip } from '$lib/plans/format';
import type { LimitKindDescriptor, Plan } from '$lib/plans/types';
import type { EntityKind, PickerBadge } from '../types';
import { byName } from './shared';

export function planLimitsSummary(plan: Plan, kinds: readonly LimitKindDescriptor[]): string {
	const parts: string[] = [];
	for (const limit of plan.limits) {
		const kind = kinds.find((k) => k.key === limit.kind);
		if (kind && limit.value !== null && limit.active !== false) parts.push(formatLimitChip(kind, limit.value));
	}
	return parts.length > 0 ? parts.join(' · ') : 'No limits';
}

export function createPlansKind(kinds: readonly LimitKindDescriptor[]): EntityKind<Plan> {
	return {
		id: 'plans',
		singular: 'plan',
		plural: 'plans',
		icon: 'layers',
		getId: (plan) => plan.id,
		getName: (plan) => plan.name,
		searchText: (plan) => [plan.name, plan.description, plan.id].filter(Boolean).join(' '),
		lead: () => ({ type: 'icon', name: 'layers' }),
		badges: (plan) => {
			const badges: PickerBadge[] = [];
			if (plan.is_default) badges.push({ label: 'default', tone: 'signal' });
			if (plan.is_system) badges.push({ label: 'system', tone: 'neutral' });
			return badges;
		},
		facts: [{ key: 'limits', label: 'Limits', value: (p) => planLimitsSummary(p, kinds), as: 'text', base: true }],
		columns: [
			{
				key: 'assigned',
				label: 'Assigned',
				width: '130px',
				mono: true,
				value: (p) => (p.assigned ? `${p.assigned.groups} grp · ${p.assigned.users} users` : '')
			}
		],
		filters: [],
		sorts: [{ value: 'name', label: 'Name', compare: byName((p: Plan) => p.name) }],
		defaultSort: 'name',
		searchPlaceholder: 'Search plans',
		empty: { title: 'No plans', description: 'Create a plan under Users, Plans first.' },
		rowHeight: 52
	};
}
