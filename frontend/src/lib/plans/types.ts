export type LimitValueType = 'bytes' | 'count' | 'usd';
export type LimitWindow = 'none' | 'day' | 'month';
export type LimitState = 'ok' | 'warn' | 'full';

export interface LimitKindDescriptor {
	key: string;
	label: string;
	short_label?: string;
	description: string;
	value_type: LimitValueType;
	unit: string;
	input_scale: number;
	format?: 'bytes' | 'count' | 'percent';
	window: LimitWindow;
	reset?: { window: string; timezone: string; label: string } | null;
	enforce_at?: string[];
	warn_at?: number;
	admin_only_values?: boolean;
	icon?: string;
	source?: string;
	plugin?: boolean;
}

export interface PlanLimit {
	kind: string;
	value: number;
	active?: boolean;
}

export interface PlanUsageSummary {
	members: number;
	kinds: { kind: string; used_total: number }[];
}

export interface Plan {
	id: string;
	name: string;
	description: string;
	is_system: boolean;
	is_default?: boolean;
	limits: PlanLimit[];
	assigned?: { groups: number; users: number };
	usage?: PlanUsageSummary;
}

export interface PlanBody {
	name: string;
	description: string;
	limits: { kind: string; value: number }[];
}

export interface PlansSettings {
	default_plan_id: string | null;
	exempt_admins: boolean;
	day_timezone: string;
	contact_line: string;
}

export interface PlansOverview {
	plans: Plan[];
	kinds: LimitKindDescriptor[];
	settings: PlansSettings;
	total: number;
}

export interface NamedRef {
	id: string;
	name: string;
}

export type LimitSourceType = 'override' | 'group' | 'default' | 'none';

export interface UsageRow {
	kind: string;
	label: string;
	format: string;
	value_type?: LimitValueType;
	window: LimitWindow;
	used: number | null;
	limit: number | null;
	remaining?: number | null;
	percent: number | null;
	state: LimitState;
	resets_at: string | null;
	enforced: boolean;
	plan: NamedRef | null;
	source: LimitSourceType;
	group: NamedRef | null;
	detail?: string;
}

export interface PlanDetail {
	plan: Plan;
	assigned_to: {
		groups: { id: string; name: string; members: number; is_default: boolean }[];
		users: { id: string; username: string }[];
	};
	in_use: {
		people: number;
		above_warn: number;
		at_limit: number;
		kinds: { kind: string; used_total: number; limit_total: number | null }[];
	};
}

export interface ResolutionStep {
	step: 'override' | 'groups' | 'default';
	plan?: NamedRef | null;
	plans?: { plan: NamedRef; group: NamedRef }[];
}

export interface UserPlanDetail {
	user: { id: string; username: string; account_type: string };
	override_plan: NamedRef | null;
	exempt: boolean;
	plan: NamedRef | null;
	source: LimitSourceType;
	group: NamedRef | null;
	limits: UsageRow[];
	resolution: ResolutionStep[];
	decided_by: 'override' | 'groups' | 'default' | 'none';
}

export interface ImpactSide {
	value: number | null;
	source: LimitSourceType;
	plan: NamedRef | null;
	group: NamedRef | null;
}

export type ImpactChange = 'raised' | 'lowered' | 'unchanged' | 'added' | 'lifted';
export type ImpactNote = 'from_this_group' | 'kept_from_other_group' | 'override' | 'default' | 'none' | 'exempt';

export interface ImpactLimit {
	kind: string;
	before: ImpactSide;
	after: ImpactSide;
	change: ImpactChange;
	note: ImpactNote;
	used: number;
	over_after: boolean;
}

export interface ImpactMember {
	user_id: string;
	username: string;
	exempt: boolean;
	override: boolean;
	limits: ImpactLimit[];
}

export interface GroupImpact {
	group: NamedRef;
	current_plan: NamedRef | null;
	proposed_plan: NamedRef | null;
	kinds: LimitKindDescriptor[];
	members: ImpactMember[];
	summary: { members: number; changed: number; over_after: number };
}

export interface UserUsageRow {
	user_id: string;
	username: string;
	email: string;
	account_type: string;
	plan: NamedRef | null;
	source: LimitSourceType;
	group: NamedRef | null;
	exempt: boolean;
	max_percent: number | null;
	limits: UsageRow[];
}

export interface UsersUsage {
	users: UserUsageRow[];
	kinds: LimitKindDescriptor[];
	total: number;
}

export interface GroupPlanRow {
	id: string;
	name: string;
	is_system: boolean;
	members: number;
	plan: NamedRef | null;
}
