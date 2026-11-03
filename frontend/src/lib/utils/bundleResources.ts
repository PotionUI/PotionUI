import type { PromptResourceKind, PromptResourceSpec } from './promptResources';

export interface BundleMember {
	kind: PromptResourceKind;
	name: string | null;
}

export interface BundleInfo {
	name: string | null;
	members: BundleMember[] | null;
}

export type BundleDirectory = Record<string, BundleInfo>;

export type BundleDirectories = Record<string, BundleDirectory>;

export interface BundleRef {
	source: string;
	itemKey: string;
}

export type BundleRow = { model: string; strength?: number };

export const MODEL_REF_PREFIX = 'model:';

const KIND_ORDER: PromptResourceKind[] = ['image', 'video', 'audio'];

const KIND_BADGES: Record<PromptResourceKind, string> = { image: 'Picture', video: 'Video', audio: 'Audio' };

function rowsOf(value: unknown): unknown[] {
	if (value === null || value === undefined || value === '') return [];
	return Array.isArray(value) ? value : [value];
}

export function isBundleSpec(spec: PromptResourceSpec | null | undefined): boolean {
	return typeof spec?.source === 'string' && spec.source !== '';
}

export function bundleFieldSpecs(specs: readonly PromptResourceSpec[]): Map<string, PromptResourceSpec> {
	const fields = new Map<string, PromptResourceSpec>();
	for (const spec of specs) {
		if (isBundleSpec(spec) && !fields.has(spec.field)) fields.set(spec.field, spec);
	}
	return fields;
}

export function isBundleRow(item: unknown): item is BundleRow {
	if (!item || typeof item !== 'object') return false;
	const record = item as Record<string, unknown>;
	return typeof record.model === 'string' && record.model !== '' && !('path' in record) && !('url' in record);
}

export function citeableBundleRows(value: unknown): BundleRow[] {
	return rowsOf(value).filter((row): row is BundleRow => {
		if (!isBundleRow(row)) return false;
		const strength = (row as { strength?: unknown }).strength;
		return typeof strength === 'number' ? strength > 0 : true;
	});
}

export function bundleItemId(itemKey: string): string {
	return itemKey.startsWith(MODEL_REF_PREFIX) ? itemKey.slice(MODEL_REF_PREFIX.length) : itemKey;
}

export function bundleRefs(specs: readonly PromptResourceSpec[], values: Record<string, unknown> | null | undefined): BundleRef[] {
	const refs = new Map<string, BundleRef>();
	for (const [field, spec] of bundleFieldSpecs(specs)) {
		for (const row of citeableBundleRows(values?.[field])) {
			refs.set(`${spec.source}\u0000${row.model}`, { source: spec.source as string, itemKey: row.model });
		}
	}
	return [...refs.values()];
}

export function bundleInfo(
	directories: BundleDirectories,
	spec: PromptResourceSpec | null | undefined,
	itemKey: string
): BundleInfo | undefined {
	return spec?.source ? directories[spec.source]?.[itemKey] : undefined;
}

export function bundleKinds(info: BundleInfo | undefined): PromptResourceKind[] {
	const present = new Set((info?.members ?? []).map((member) => member.kind));
	return KIND_ORDER.filter((kind) => present.has(kind));
}

export function bundleSourceBadge(spec: PromptResourceSpec | null | undefined): string {
	return spec?.source_badge || spec?.source_label || spec?.source || '';
}

export function bundleSourceLabel(spec: PromptResourceSpec | null | undefined): string {
	return spec?.source_label || spec?.source || '';
}

export function bundleBadge(info: BundleInfo | undefined, spec: PromptResourceSpec | null | undefined): string {
	const kinds = bundleKinds(info);
	if (kinds.length === 0) return bundleSourceBadge(spec);
	return kinds.map((kind) => KIND_BADGES[kind]).join(' + ');
}

export function bundleName(itemKey: string, info: BundleInfo | undefined): string {
	return info?.name || bundleItemId(itemKey);
}

export function bundleMemberKinds(info: BundleInfo | undefined): PromptResourceKind[] {
	const members = info?.members ?? [];
	return members.length > 0 ? members.map((member) => member.kind) : ['image'];
}

export function bundleCopies(row: unknown): number {
	const raw = row && typeof row === 'object' ? (row as Record<string, unknown>).copies : undefined;
	const value = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() ? Number(raw) : 1;
	return Number.isInteger(value) && value >= 1 ? value : 1;
}

export function bundlePrimaryKind(info: BundleInfo | undefined): PromptResourceKind {
	return bundleKinds(info)[0] ?? 'image';
}
