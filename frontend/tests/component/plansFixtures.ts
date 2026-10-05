import type { GroupImpact, ImpactLimit, LimitKindDescriptor, Plan, UsageRow, UserPlanDetail } from '../../src/lib/plans/types';

export const GB = 1024 ** 3;

export const KINDS: LimitKindDescriptor[] = [
	{
		key: 'storage_bytes',
		label: 'Storage space',
		short_label: 'Storage',
		description: 'Generations and uploads, measured live',
		value_type: 'bytes',
		unit: 'GB',
		input_scale: GB,
		window: 'none'
	},
	{
		key: 'generations_per_day',
		label: 'Generations per day',
		short_label: 'Generations today',
		description: 'Submits per user',
		value_type: 'count',
		unit: 'per day',
		input_scale: 1,
		window: 'day'
	},
	{
		key: 'cloud_spend_usd_month',
		label: 'Cloud spend per month',
		short_label: 'Cloud budget',
		description: 'Counts cloud-provider cost',
		value_type: 'usd',
		unit: 'USD / month',
		input_scale: 1,
		window: 'month',
		admin_only_values: true
	},
	{
		key: 'example.credits',
		label: 'Image-gen plugin: Credits',
		description: 'Added by an enabled plugin',
		value_type: 'count',
		unit: 'credits',
		input_scale: 1,
		window: 'none',
		plugin: true
	}
];

export const PLANS: Plan[] = [
	{ id: 'free', name: 'Free', description: '', is_system: false, limits: [{ kind: 'storage_bytes', value: 5 * GB }, { kind: 'generations_per_day', value: 20 }] },
	{ id: 'tier1', name: 'Tier 1', description: '', is_system: false, limits: [{ kind: 'storage_bytes', value: 20 * GB }, { kind: 'generations_per_day', value: 100 }] },
	{ id: 'unlimited', name: 'Unlimited', description: '', is_system: true, limits: [] }
];

function side(value: number | null, group: string | null = null) {
	return { value, source: 'group' as const, plan: null, group: group ? { id: group, name: group } : null };
}

function limit(partial: Partial<ImpactLimit>): ImpactLimit {
	return { kind: 'storage_bytes', before: side(5 * GB), after: side(20 * GB), change: 'raised', note: 'from_this_group', used: 0, over_after: false, ...partial };
}

export const IMPACT: GroupImpact = {
	group: { id: 'g1', name: 'premium-tier-1' },
	current_plan: null,
	proposed_plan: { id: 'tier1', name: 'Tier 1' },
	kinds: [KINDS[0], KINDS[1]],
	summary: { members: 3, changed: 2, over_after: 1 },
	members: [
		{
			user_id: 'u1',
			username: 'jonas',
			exempt: false,
			override: false,
			limits: [limit({}), limit({ kind: 'generations_per_day', before: side(20), after: side(100) })]
		},
		{
			user_id: 'u2',
			username: 'mira',
			exempt: false,
			override: false,
			limits: [
				limit({ before: side(100 * GB), after: side(100 * GB, 'premium-tier-2'), change: 'unchanged', note: 'kept_from_other_group' }),
				limit({ kind: 'generations_per_day', before: side(500), after: side(500, 'premium-tier-2'), change: 'unchanged', note: 'kept_from_other_group' })
			]
		},
		{
			user_id: 'u3',
			username: 'lena',
			exempt: false,
			override: false,
			limits: [limit({ before: side(null), after: side(1 * GB), change: 'added', over_after: true }), limit({ kind: 'generations_per_day', before: side(20), after: side(100) })]
		}
	]
};

function usage(partial: Partial<UsageRow>): UsageRow {
	return {
		kind: 'storage_bytes',
		label: 'Storage',
		format: 'bytes',
		window: 'none',
		used: 0,
		limit: null,
		percent: null,
		state: 'ok',
		resets_at: null,
		enforced: true,
		plan: { id: 'tier1', name: 'Tier 1' },
		source: 'group',
		group: { id: 'g1', name: 'premium-tier-1' },
		...partial
	};
}

export const USER_DETAIL: UserPlanDetail = {
	user: { id: 'u1', username: 'jonas', account_type: 'USER' },
	override_plan: null,
	exempt: false,
	plan: { id: 'tier1', name: 'Tier 1' },
	source: 'group',
	group: { id: 'g1', name: 'premium-tier-1' },
	limits: [
		usage({ used: 18.6 * GB, limit: 20 * GB, percent: 93, state: 'warn', detail: 'Tier 1 via premium-tier-1; All users says 5 GB, smaller' }),
		usage({ kind: 'generations_per_day', label: 'Generations', format: 'count', window: 'day', used: 37, limit: 100, percent: 37 })
	],
	resolution: [
		{ step: 'override', plan: null },
		{ step: 'groups', plans: [{ plan: { id: 'tier1', name: 'Tier 1' }, group: { id: 'g1', name: 'premium-tier-1' } }] },
		{ step: 'default', plan: { id: 'free', name: 'Free' } }
	],
	decided_by: 'groups'
};

export async function openSelectAndPick(container: HTMLElement, label: string) {
	const trigger = container.querySelector('button[aria-haspopup="listbox"]') as HTMLElement;
	trigger.click();
	const { tick, flushSync } = await import('svelte');
	await tick();
	flushSync();
	const option = Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]')).find((o) => o.textContent?.includes(label));
	if (!option) throw new Error(`option ${label} not found`);
	option.click();
	await tick();
	flushSync();
}
