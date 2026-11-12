import { acceptedFrom, formatItemCount, usageOf, type AcceptedKinds, type Usage } from './kitCounts';
import { isKitRow } from './kitMarker';
import type { KitDirectory } from './kitChips';
import type { KitDetail, KitItem, KitRow } from './types';
import { kitPathLabel, parseKitQuery } from './kitPath';

export const KITS_GROUP = '__kits__';

export interface PromptSpecLike {
	field: string;
	kind: string;
	source?: string;
}

export interface KitField {
	field: string;
	accepted: AcceptedKinds;
	rows: KitRow[];
}

export function kitCapableFields(specs: readonly PromptSpecLike[]): Map<string, AcceptedKinds> {
	const kinds = new Map<string, string[]>();
	for (const spec of specs) {
		if (spec.source) continue;
		kinds.set(spec.field, [...(kinds.get(spec.field) ?? []), spec.kind]);
	}
	return new Map([...kinds].map(([field, list]) => [field, acceptedFrom(list)]));
}

export function kitFieldsOf(specs: readonly PromptSpecLike[], values: Record<string, unknown> | null | undefined): KitField[] {
	const capable = kitCapableFields(specs);
	return [...capable].map(([field, accepted]) => {
		const value = values?.[field];
		const rows = Array.isArray(value) ? value.filter(isKitRow) : [];
		return { field, accepted, rows };
	});
}

export interface LoadedKit {
	field: string;
	kit: KitDetail;
	accepted: AcceptedKinds;
	usage: Usage;
}

export function loadedKits(fields: readonly KitField[], directory: KitDirectory): LoadedKit[] {
	const found: LoadedKit[] = [];
	for (const entry of fields) {
		for (const row of entry.rows) {
			const kit = directory.get(row.kit);
			if (kit) found.push({ field: entry.field, kit, accepted: entry.accepted, usage: usageOf(kit.items, entry.accepted) });
		}
	}
	return found;
}

export function kitCapable(fields: readonly KitField[]): boolean {
	return fields.length > 0;
}

export function defaultKitField(fields: readonly KitField[]): string | null {
	return fields.find((entry) => entry.rows.length > 0)?.field ?? fields[0]?.field ?? null;
}

export interface KitPickerState {
	kitMode: boolean;
	drilled: LoadedKit | null;
	filter: string;
}

export function kitPickerState(
	group: string | null,
	drilledId: string | null,
	query: string,
	loaded: readonly LoadedKit[]
): KitPickerState {
	if (group === KITS_GROUP) {
		const drilled = drilledId ? (loaded.find((entry) => entry.kit.id === drilledId) ?? null) : null;
		return { kitMode: true, drilled, filter: query.trim().toLowerCase() };
	}
	if (group !== null) return { kitMode: false, drilled: null, filter: '' };
	const typed = parseKitQuery(query);
	if (!typed) return { kitMode: false, drilled: null, filter: '' };
	if (typed.drilled) {
		const drilled = loaded.find((entry) => entry.kit.handle === typed.handle) ?? null;
		return { kitMode: true, drilled, filter: (typed.item ?? '').toLowerCase() };
	}
	return { kitMode: true, drilled: null, filter: (typed.handle ?? '').toLowerCase() };
}

export function kitMatches(entry: LoadedKit, filter: string): boolean {
	if (!filter) return true;
	return entry.kit.name.toLowerCase().includes(filter) || entry.kit.handle.includes(filter);
}

export function itemMatches(item: KitItem, filter: string): boolean {
	return !filter || item.name.includes(filter);
}

export function kitRowDescription(entry: LoadedKit): string {
	const count = formatItemCount(entry.kit.items.length);
	return entry.kit.label ? `${count} · ${entry.kit.label}` : count;
}

export function bareKitDescription(entry: LoadedKit): string {
	const total = entry.kit.items.length;
	const used = entry.usage.used.length;
	return used === total ? `Everything this model can take · ${total}` : `Everything this model can take · ${used} of ${total}`;
}

export function itemDescription(item: KitItem, accepted: AcceptedKinds): string {
	const kind = item.kind[0].toUpperCase() + item.kind.slice(1);
	if (item.missing) return 'File deleted';
	if (accepted && item.kind !== 'text' && !accepted.has(item.kind)) return `${kind} · not taken by this model`;
	if (item.kind === 'text') return `Text · ${(item.text ?? '').length} characters`;
	if (item.kind === 'image' && item.width && item.height) return `${kind} · ${item.width}×${item.height}`;
	return kind;
}

export function kitPathText(entry: LoadedKit, item?: KitItem): string {
	return kitPathLabel(entry.kit.handle, item?.name);
}

export function groupDescription(count: number): string {
	return count === 0 ? 'No kits loaded yet' : `${count} loaded`;
}
