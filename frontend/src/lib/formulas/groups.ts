import type { FieldIndex } from './fieldIndex';
import { formatValue } from './format';
import type { FormulaDeclaration, FormulaDraft } from './types';

export interface GroupRow {
	name: string;
	label: string;
	text: string;
	advanced: boolean;
	changed: boolean;
}

export interface GroupState {
	id: string;
	label: string;
	description: string | null;
	preselect: boolean;
	rows: GroupRow[];
	count: number;
	changed: boolean;
	summary: string;
}

export function sameValue(a: unknown, b: unknown): boolean {
	if (a === b) return true;
	const blank = (v: unknown) => v === undefined || v === null || v === '';
	if (blank(a) && blank(b)) return true;
	return JSON.stringify(a) === JSON.stringify(b);
}

const SUMMARY_LIMIT = 4;

export function groupStates(
	declaration: FormulaDeclaration,
	index: FieldIndex,
	formData: Record<string, unknown>,
	defaults: Record<string, unknown>
): GroupState[] {
	const states: GroupState[] = [];
	for (const group of declaration.groups) {
		const rows: GroupRow[] = [];
		for (const name of group.fields) {
			const field = index.get(name);
			if (!field || !(name in formData)) continue;
			rows.push({
				name,
				label: field.label,
				text: formatValue(field, formData[name]),
				advanced: field.advanced,
				changed: !sameValue(formData[name], defaults[name])
			});
		}
		if (rows.length === 0) continue;
		states.push({
			id: group.id,
			label: group.label,
			description: group.description ?? null,
			preselect: group.preselect !== false,
			rows,
			count: rows.length,
			changed: rows.some((row) => row.changed),
			summary: rows
				.slice(0, SUMMARY_LIMIT)
				.map((row) => row.text)
				.join(' · ')
		});
	}
	return states;
}

export function preselectedIds(states: GroupState[]): string[] {
	return states.filter((state) => state.preselect && state.changed).map((state) => state.id);
}

function isCompanion(key: string, names: Set<string>): boolean {
	for (const name of names) {
		if (key === `${name}_tagFilters` || key === `${name}_inpaint_mask` || key.startsWith(`${name}__`)) return true;
	}
	return false;
}

export function buildDraft(args: {
	presetId: string;
	mode: string;
	variant: string | null;
	presetVersion: string;
	name: string;
	note?: string | null;
	declaration: FormulaDeclaration;
	selected: ReadonlySet<string>;
	index: FieldIndex;
	formData: Record<string, unknown>;
}): FormulaDraft {
	const groups: Array<{ id: string }> = [];
	const values: Record<string, unknown> = {};
	const names = new Set<string>();
	for (const group of args.declaration.groups) {
		if (!args.selected.has(group.id)) continue;
		const present = group.fields.filter((name) => args.index.has(name) && name in args.formData);
		if (present.length === 0) continue;
		groups.push({ id: group.id });
		for (const name of present) {
			values[name] = args.formData[name];
			names.add(name);
		}
	}
	for (const [key, value] of Object.entries(args.formData)) {
		if (!(key in values) && isCompanion(key, names)) values[key] = value;
	}
	return {
		preset_id: args.presetId,
		mode: args.mode,
		variant: args.variant,
		name: args.name.trim(),
		note: args.note ?? null,
		groups,
		values,
		preset_version: args.presetVersion
	};
}

export function countSelected(states: GroupState[], selected: ReadonlySet<string>): number {
	return states.filter((state) => selected.has(state.id)).reduce((total, state) => total + state.count, 0);
}

export function filterFormulas<T extends { name: string }>(items: T[], query: string): T[] {
	const q = query.trim().toLowerCase();
	return q ? items.filter((item) => item.name.toLowerCase().includes(q)) : items;
}

export function nextCopyName(name: string, taken: string[]): string {
	const base = `${name} copy`;
	if (!taken.includes(base)) return base;
	let n = 2;
	while (taken.includes(`${base} ${n}`)) n += 1;
	return `${base} ${n}`;
}
