export interface FormulaGroupDecl {
	id: string;
	label: string;
	description?: string | null;
	preselect?: boolean;
	fields: string[];
}

export interface FormulaDeclaration {
	groups: FormulaGroupDecl[];
}

export interface FormulaGroupRef {
	id: string;
	label: string;
	fields: string[];
}

export interface Formula {
	id: string;
	preset_id: string;
	mode: string;
	variant?: string | null;
	name: string;
	note?: string | null;
	groups: FormulaGroupRef[];
	values: Record<string, unknown>;
	signatures?: Record<string, unknown>;
	preset_version?: string | null;
	created_at: string;
	updated_at: string;
}

export interface FormulaContent {
	variant: string | null;
	groups: Array<{ id: string }>;
	values: Record<string, unknown>;
	preset_version: string;
}

export interface FormulaDraft extends FormulaContent {
	preset_id: string;
	mode: string;
	name: string;
	note?: string | null;
}

export type LoraMode = 'replace' | 'add';

export interface PlanLoraRow {
	model: string;
	status: 'added' | 'removed' | 'changed' | 'same';
	old: Record<string, unknown> | null;
	new: Record<string, unknown> | null;
}

export interface PlanChange {
	field: string;
	label: string;
	group: string;
	groupLabel: string;
	old: unknown;
	new: unknown;
	advanced: boolean;
	type?: string;
	companionOf?: string;
	rows?: PlanLoraRow[];
}

export interface PlanSame {
	field: string;
	label: string;
	group: string;
}

export interface PlanSkip {
	field: string;
	label: string;
	group: string;
	code: string;
	reason: string;
	row?: string;
	library?: boolean;
}

export interface ServerPlan {
	changes: PlanChange[];
	same: PlanSame[];
	skips: PlanSkip[];
}

export interface AppliedSnapshot {
	values: Record<string, unknown>;
	absent: string[];
}

export interface AppliedChange {
	old: unknown;
	new: unknown;
	label: string;
	type?: string;
}

export interface AppliedState {
	formulaId: string;
	name: string;
	key: string;
	revision: number;
	changed: Record<string, AppliedChange>;
	skipped: number;
	snapshot: AppliedSnapshot;
}

export type MenuAction = 'rename' | 'duplicate' | 'update' | 'delete';
