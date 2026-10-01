import { loraKey, loraName, loraStrength } from './format';
import type { AppliedChange, AppliedSnapshot, PlanChange, PlanSame, PlanSkip, ServerPlan } from './types';

export type CapabilityCheck = (patch: Record<string, unknown>) => Record<string, string[]>;

export interface ApplyPlan {
	changes: PlanChange[];
	same: PlanSame[];
	skips: PlanSkip[];
}

export const CAPABILITY_REASON = 'Not valid for the selected model';

export function orderControllersFirst<T extends { field: string }>(
	items: T[],
	dependencies: Record<string, string[]>
): T[] {
	const names = new Set(items.map((item) => item.field));
	const byName = new Map(items.map((item) => [item.field, item]));
	const placed = new Set<string>();
	const ordered: T[] = [];
	const visiting = new Set<string>();
	const place = (item: T) => {
		if (placed.has(item.field) || visiting.has(item.field)) return;
		visiting.add(item.field);
		for (const controller of dependencies[item.field] ?? []) {
			if (names.has(controller)) place(byName.get(controller)!);
		}
		visiting.delete(item.field);
		placed.add(item.field);
		ordered.push(item);
	};
	for (const item of items) place(item);
	return ordered;
}

function dropInvalidKeys(value: unknown, keys: string[]): Record<string, unknown> {
	const next: Record<string, unknown> = { ...(value as Record<string, unknown>) };
	for (const key of keys) delete next[key];
	return next;
}

export function planApply(
	server: ServerPlan,
	options: {
		dependencies?: Record<string, string[]>;
		capabilityInvalid?: CapabilityCheck;
	} = {}
): ApplyPlan {
	const skips: PlanSkip[] = [...(server.skips ?? [])];
	let changes: PlanChange[] = [];
	const patch: Record<string, unknown> = {};
	for (const change of server.changes ?? []) patch[change.field] = change.new;
	const invalid = options.capabilityInvalid ? options.capabilityInvalid(patch) : {};

	for (const change of server.changes ?? []) {
		const bad = invalid[change.field];
		if (!bad) {
			changes.push(change);
			continue;
		}
		const wholeField = bad.length === 0 || typeof change.new !== 'object' || change.new === null || Array.isArray(change.new);
		if (wholeField) {
			skips.push({ field: change.field, label: change.label, group: change.group, code: 'capability', reason: CAPABILITY_REASON });
			continue;
		}
		const remaining = dropInvalidKeys(change.new, bad);
		for (const key of bad) {
			skips.push({ field: change.field, label: `${change.label} · ${key}`, group: change.group, code: 'capability', reason: CAPABILITY_REASON, row: key });
		}
		if (Object.keys(remaining).length > 0) changes.push({ ...change, new: remaining });
	}

	changes = orderControllersFirst(changes, options.dependencies ?? {});
	return { changes, same: server.same ?? [], skips };
}

export interface ApplyResult {
	formData: Record<string, unknown>;
	snapshot: AppliedSnapshot;
	changed: Record<string, AppliedChange>;
}

export function applyPlan(
	formData: Record<string, unknown>,
	plan: ApplyPlan,
	selected: ReadonlySet<string>
): ApplyResult {
	const next: Record<string, unknown> = { ...formData };
	const snapshot: AppliedSnapshot = { values: {}, absent: [] };
	const changed: Record<string, AppliedChange> = {};
	for (const change of plan.changes) {
		if (!selected.has(change.companionOf ?? change.field)) continue;
		const companion = !!change.companionOf;
		if (change.field in formData) snapshot.values[change.field] = formData[change.field];
		else snapshot.absent.push(change.field);
		next[change.field] = change.new;
		if (companion) continue;
		changed[change.field] = {
			old: change.field in formData ? formData[change.field] : undefined,
			new: change.new,
			label: change.label,
			type: change.type
		};
	}
	return { formData: next, snapshot, changed };
}

export function appliedKey(tab: {
	selectedPreset?: string | null;
	selectedMode?: string | null;
	selectedVariant?: string | null;
	selectedSessionId?: string | null;
}): string {
	return [tab.selectedPreset, tab.selectedMode, tab.selectedVariant, tab.selectedSessionId].map((part) => part ?? '').join('|');
}

export function undoApplied(formData: Record<string, unknown>, snapshot: AppliedSnapshot): Record<string, unknown> {
	const next: Record<string, unknown> = { ...formData };
	for (const [field, value] of Object.entries(snapshot.values)) next[field] = value;
	for (const field of snapshot.absent) delete next[field];
	return next;
}

export type LoraRowStatus = 'added' | 'removed' | 'changed' | 'same';

export interface LoraRowDiff {
	key: string;
	name: string;
	status: LoraRowStatus;
	oldStrength: number | null;
	newStrength: number | null;
}

export function loraRowsOf(change: PlanChange): LoraRowDiff[] {
	if (!change.rows) return diffLoraRows(change.old, change.new);
	return change.rows.map((row) => ({
		key: row.model,
		name: loraName(row.new ?? row.old),
		status: row.status,
		oldStrength: loraStrength(row.old),
		newStrength: loraStrength(row.new)
	}));
}

export function diffLoraRows(oldRows: unknown, newRows: unknown): LoraRowDiff[] {
	const before = Array.isArray(oldRows) ? oldRows : [];
	const after = Array.isArray(newRows) ? newRows : [];
	const beforeByKey = new Map(before.map((row) => [loraKey(row), row]));
	const afterKeys = new Set(after.map((row) => loraKey(row)));
	const diffs: LoraRowDiff[] = [];
	for (const row of before) {
		if (!afterKeys.has(loraKey(row))) {
			diffs.push({ key: loraKey(row), name: loraName(row), status: 'removed', oldStrength: loraStrength(row), newStrength: null });
		}
	}
	for (const row of after) {
		const key = loraKey(row);
		const prior = beforeByKey.get(key);
		if (prior === undefined) {
			diffs.push({ key, name: loraName(row), status: 'added', oldStrength: null, newStrength: loraStrength(row) });
		} else {
			const same = JSON.stringify(prior) === JSON.stringify(row);
			diffs.push({
				key,
				name: loraName(row),
				status: same ? 'same' : 'changed',
				oldStrength: loraStrength(prior),
				newStrength: loraStrength(row)
			});
		}
	}
	return diffs;
}
