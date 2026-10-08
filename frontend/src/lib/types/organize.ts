export type OrganizeSubject = 'generation' | 'upload' | 'model';
export type OrganizeMatch = 'all' | 'any';
export type OrganizeRuleStatus = 'active' | 'off' | 'paused' | 'needs_attention';
export type OrganizePausedReason =
	| 'rate_limited'
	| 'collection_missing'
	| 'model_restricted'
	| 'admin_paused'
	| null;
export type OrganizeCollectionScope = 'history' | 'library' | 'models';

export interface OrganizeSubjectInfo {
	key: OrganizeSubject;
	label: string;
	trigger: string;
	trigger_label: string;
	collection_scope: OrganizeCollectionScope;
	supports_tags: boolean;
}

export interface OrganizeOption {
	value: string;
	label: string;
	meta?: Record<string, unknown>;
}

export type OrganizeAttributeType = 'number' | 'text' | 'bool' | 'enum';

export interface OrganizeAttributeMeta {
	type: OrganizeAttributeType;
	type_label?: string;
	field_type?: string;
	model_types?: string[];
	choices?: OrganizeOption[];
	min?: number;
	max?: number;
	step?: number;
}

export interface OrganizeAttributeOption extends OrganizeOption {
	meta: OrganizeAttributeMeta & Record<string, unknown>;
}

export interface OrganizeAttributeValue {
	key: string;
	type: OrganizeAttributeType | '';
	label: string;
	value: unknown;
}

export interface OrganizeSizePreset {
	label: string;
	width: number;
	height: number;
}

export interface OrganizePicker {
	model_types?: string[];
	multi?: boolean;
	presets?: OrganizeSizePreset[];
	min?: number;
	max?: number;
	step?: number;
	unit?: string;
	placeholder?: string;
	tag_type?: 'GENERATION' | 'UPLOAD' | 'MODEL';
	operators_by_type?: Partial<Record<OrganizeAttributeType, string[]>>;
	[key: string]: unknown;
}

export interface OrganizeFactSpec {
	key: string;
	label: string;
	subjects: OrganizeSubject[];
	kind: string;
	operators: string[];
	picker: OrganizePicker | null;
	options: OrganizeOption[] | null;
	has_options_endpoint: boolean;
	description: string;
	source: string;
	component: string | null;
	previewable: boolean;
}

export interface OrganizeConfigField {
	key: string;
	kind: string;
	label: string;
	required?: boolean;
	default?: unknown;
	options?: OrganizeOption[];
	min?: number;
	max?: number;
	step?: number;
}

export interface OrganizeActionSpec {
	key: string;
	label: string;
	subjects: OrganizeSubject[];
	config_schema: OrganizeConfigField[];
	requires_admin: boolean;
	undoable: boolean;
	source: string;
	component: string | null;
}

export interface OrganizeCatalog {
	subjects: OrganizeSubjectInfo[];
	kinds: Record<string, { operators: string[] }>;
	operators: Record<string, string>;
	facts: OrganizeFactSpec[];
	actions: OrganizeActionSpec[];
}

export interface OrganizeCondition {
	fact: string;
	operator: string;
	value: unknown;
}

export interface OrganizeAction {
	action: string;
	config: Record<string, unknown>;
}

export interface OrganizeIssue {
	code: string;
	message: string;
	blocking: boolean;
	path?: string;
}

export interface OrganizeRule {
	id: string;
	name: string;
	subject: OrganizeSubject;
	trigger: string;
	match: OrganizeMatch;
	conditions: OrganizeCondition[];
	actions: OrganizeAction[];
	enabled: boolean;
	stop_after: boolean;
	position: number;
	status: OrganizeRuleStatus;
	paused_reason: OrganizePausedReason;
	paused_at: string | null;
	issues: OrganizeIssue[];
	targets: { collections: { id: string; name: string; exists: boolean }[] };
	filed_count: number;
	last_run_at: string | null;
	created_at: string;
	updated_at: string;
}

export interface OrganizeRuleInput {
	name: string;
	subject: OrganizeSubject;
	match: OrganizeMatch;
	conditions: OrganizeCondition[];
	actions: OrganizeAction[];
	enabled: boolean;
	stop_after: boolean;
}

export interface OrganizeSummary {
	paused_by_admin: boolean;
	rule_cap: number;
	rule_count: number;
	hourly_limit: number;
	subjects: Record<OrganizeSubject, { total: number; active: number; needs_attention: number }>;
}

export interface OrganizePreviewRequest {
	subject: OrganizeSubject;
	match: OrganizeMatch;
	conditions: OrganizeCondition[];
	actions?: OrganizeAction[];
	rule_id?: string | null;
}

export interface OrganizePreview {
	matched: number;
	already_handled: number;
	would_change: number;
	approximate: boolean;
	sample: { item_type: OrganizeSubject; item_id: string }[];
	duplicates: { rule_id: string; name: string }[];
}

export type OrganizeJobStatus = 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';

export interface OrganizeJob {
	id: string;
	rule_id: string;
	rule_name: string;
	status: OrganizeJobStatus;
	total: number;
	processed: number;
	applied: number;
	run_id: string | null;
	error: string | null;
	started_at: string;
	finished_at: string | null;
}

export type OrganizeRunStatus = 'running' | 'completed' | 'cancelled' | 'failed' | 'undone';

export interface OrganizeRun {
	id: string;
	rule_id: string;
	rule_name: string;
	rule_deleted: boolean;
	subject: OrganizeSubject;
	kind: 'live' | 'backfill';
	status: OrganizeRunStatus;
	started_at: string;
	finished_at: string | null;
	matched: number;
	applied: number;
	undone: number;
	can_undo: boolean;
	changes: {
		collections: { id: string; name: string; count: number; live: number; created: boolean }[];
		tags: { id: string; name: string; count: number; live: number }[];
		other: { action: string; label: string; count: number; live: number }[];
	};
}

export interface OrganizeActivityPage {
	runs: OrganizeRun[];
	next_before: string | null;
}

export interface OrganizeUndoResult {
	undone: number;
	items: number;
	skipped: number;
	run: OrganizeRun;
}

export interface OrganizeProvenance {
	rule_id: string;
	rule_name: string;
	rule_deleted: boolean;
	run_id: string;
	action: string;
	target_type: 'collection' | 'model_collection' | 'tag' | 'plugin';
	target_id: string;
	target_name: string;
	created_at: string;
}

export interface OrganizeCollectionRuleRef {
	id: string;
	name: string;
	status: OrganizeRuleStatus;
}

export interface OrganizeAdminUser {
	user_id: string;
	username: string;
	rules: number;
	enabled_rules: number;
	paused_rules: number;
	items_filed_24h: number;
	items_filed_total: number;
	paused: boolean;
	rule_cap: number | null;
	effective_rule_cap: number;
	last_run_at: string | null;
}

export interface OrganizeAdminOverview {
	paused_all: boolean;
	default_rule_cap: number;
	hourly_limit: number;
	totals: {
		users_with_rules: number;
		rules: number;
		enabled_rules: number;
		paused_rules: number;
		items_filed_24h: number;
		items_filed_total: number;
		running_jobs: number;
	};
	users: OrganizeAdminUser[];
}

export interface OrganizeProblem {
	path: string;
	code: string;
	message: string;
}
