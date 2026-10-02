import { resourceMarkerRegex } from '$lib/utils/promptResources';

interface MergeFromField {
	name?: unknown;
	merge_from?: unknown;
	children?: unknown;
}

function collectMergeFrom(fields: unknown, out: Map<string, string[]>): void {
	if (!Array.isArray(fields)) return;
	for (const field of fields as MergeFromField[]) {
		if (!field || typeof field !== 'object') continue;
		if (typeof field.name === 'string' && Array.isArray(field.merge_from)) {
			const keys = field.merge_from.filter((key): key is string => typeof key === 'string' && key !== '');
			if (keys.length) out.set(field.name, keys);
		}
		collectMergeFrom(field.children, out);
	}
}

export function schemaMergeFrom(schema: { properties?: Record<string, unknown> } | null | undefined): Map<string, string[]> {
	const out = new Map<string, string[]>();
	for (const root of Object.values(schema?.properties ?? {})) {
		collectMergeFrom((root as MergeFromField | null)?.children, out);
	}
	return out;
}

export function schemaMergeFromAliases(
	schema: { properties?: Record<string, unknown> } | null | undefined
): Record<string, string> {
	const aliases: Record<string, string> = {};
	for (const [name, keys] of schemaMergeFrom(schema)) {
		for (const key of keys) {
			if (!(key in aliases)) aliases[key] = name;
		}
	}
	return aliases;
}

export function rewriteMergedMarkers<T>(value: T, aliases: Record<string, string>): T {
	if (!Object.keys(aliases).length) return value;
	if (typeof value === 'string') {
		if (!value.includes('@[')) return value;
		const next = value.replace(resourceMarkerRegex(), (marker, field: string, itemKey: string) =>
			Object.prototype.hasOwnProperty.call(aliases, field) ? `@[${aliases[field]}:${itemKey}]` : marker
		);
		return (next === value ? value : next) as T;
	}
	if (Array.isArray(value)) {
		let changed = false;
		const next = value.map((item) => {
			const rewritten = rewriteMergedMarkers(item, aliases);
			if (rewritten !== item) changed = true;
			return rewritten;
		});
		return (changed ? next : value) as T;
	}
	if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
		const record = value as Record<string, unknown>;
		let changed = false;
		const next: Record<string, unknown> = {};
		for (const [key, item] of Object.entries(record)) {
			const rewritten = rewriteMergedMarkers(item, aliases);
			if (rewritten !== item) changed = true;
			next[key] = rewritten;
		}
		const field = next.field;
		if (typeof field === 'string' && Object.prototype.hasOwnProperty.call(aliases, field) && 'item_key' in next) {
			next.field = aliases[field];
			changed = true;
		}
		return (changed ? next : value) as T;
	}
	return value;
}

function asItems(value: unknown): unknown[] {
	if (value === null || value === undefined || value === '') return [];
	return Array.isArray(value) ? value : [value];
}

export function applyMergeFrom(
	schema: { properties?: Record<string, unknown> } | null | undefined,
	data: Record<string, unknown> | null
): Record<string, unknown> | null {
	if (!data) return data;
	let next: Record<string, unknown> | null = null;
	for (const [name, keys] of schemaMergeFrom(schema)) {
		const present = keys.filter((key) => Object.prototype.hasOwnProperty.call(next ?? data, key));
		if (!present.length) continue;
		next ??= { ...data };
		next[name] = [...asItems(next[name]), ...keys.flatMap((key) => asItems(next![key]))];
		for (const key of present) delete next[key];
	}
	return rewriteMergedMarkers(next ?? data, schemaMergeFromAliases(schema));
}
