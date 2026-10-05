export type LimitValueType = 'bytes' | 'count' | 'usd';
export type LimitWindow = 'none' | 'day' | 'month';

export interface LimitKindDescriptor {
	key: string;
	label: string;
	description: string;
	value_type: LimitValueType;
	unit: string;
	window: LimitWindow;
	admin_only_values?: boolean;
	plugin?: string | null;
	active?: boolean;
	icon?: string;
}

export interface PlanLimit {
	kind: string;
	value: number;
}

export interface PlanUsageSummary {
	kind: string;
	used: number;
	limit: number | null;
	over_warn_count?: number;
}

export interface Plan {
	id: string;
	name: string;
	description: string;
	limits: PlanLimit[];
	is_system: boolean;
	group_count?: number;
	user_count?: number;
	member_count?: number;
	usage?: PlanUsageSummary[];
}

export interface PlanBody {
	name: string;
	description: string;
	limits: PlanLimit[];
}

export interface PlansSettings {
	default_plan_id: string | null;
	admins_exempt: boolean;
	day_timezone: string;
	contact_line: string;
}

export type LimitSourceType = 'override' | 'group' | 'default' | 'exempt' | 'none';

export interface LimitSource {
	type: LimitSourceType;
	plan_id?: string | null;
	plan_name?: string | null;
	group_id?: string | null;
	group_name?: string | null;
}

export interface EffectiveLimit {
	kind: string;
	limit: number | null;
	used: number;
	source: LimitSource;
}

export interface UserEffectiveLimits {
	user_id: string;
	override_plan_id: string | null;
	plan_name: string | null;
	limits: EffectiveLimit[];
}

export interface ImpactCell {
	kind: string;
	before: number | null;
	after: number | null;
	kept: boolean;
	over: boolean;
}

export interface ImpactRow {
	user_id: string;
	username: string;
	cells: ImpactCell[];
	note: string;
}

export interface GroupImpact {
	group_id: string;
	plan_id: string | null;
	rows: ImpactRow[];
}

export interface UserUsageRow {
	user_id: string;
	plan_name: string | null;
	source: LimitSourceType;
	limits: { kind: string; used: number; limit: number | null }[];
}
