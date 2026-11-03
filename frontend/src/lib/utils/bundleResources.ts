import type { PromptResourceKind, PromptResourceSpec } from './promptResources';

export interface RefModMember {
	kind: PromptResourceKind;
	name: string | null;
}

export interface RefModInfo {
	name: string | null;
	members: RefModMember[] | null;
}

export type RefModDirectory = Record<string, RefModInfo>;

export const MODEL_REF_PREFIX = 'model:';

export const REFMOD_FALLBACK_BADGE = 'RefMod';

const KIND_ORDER: PromptResourceKind[] = ['image', 'video', 'audio'];

const KIND_BADGES: Record<PromptResourceKind, string> = { image: 'Picture', video: 'Video', audio: 'Audio' };

function rowsOf(value: unknown): unknown[] {
	if (value === null || value === undefined || value === '') return [];
	return Array.isArray(value) ? value : [value];
}

export function isRefModSpec(spec: PromptResourceSpec | null | undefined): boolean {
	return spec?.source === 'refmod';
}

export function refModFields(specs: readonly PromptResourceSpec[]): Set<string> {
	return new Set(specs.filter(isRefModSpec).map((spec) => spec.field));
}

export function isRefModRow(item: unknown): item is { model: string; strength?: number } {
	if (!item || typeof item !== 'object') return false;
	const record = item as Record<string, unknown>;
	return typeof record.model === 'string' && record.model !== '' && !('path' in record) && !('url' in record);
}

export function citeableRefModRows(value: unknown): Array<{ model: string; strength?: number }> {
	return rowsOf(value).filter((row): row is { model: string; strength?: number } => {
		if (!isRefModRow(row)) return false;
		const strength = (row as { strength?: unknown }).strength;
		return typeof strength === 'number' ? strength > 0 : true;
	});
}

export function refModId(modelRef: string): string {
	return modelRef.startsWith(MODEL_REF_PREFIX) ? modelRef.slice(MODEL_REF_PREFIX.length) : modelRef;
}

export function refModIds(specs: readonly PromptResourceSpec[], values: Record<string, unknown> | null | undefined): string[] {
	const ids = new Set<string>();
	for (const field of refModFields(specs)) {
		for (const row of citeableRefModRows(values?.[field])) ids.add(refModId(row.model));
	}
	return [...ids];
}

export function refModKinds(info: RefModInfo | undefined): PromptResourceKind[] {
	const present = new Set((info?.members ?? []).map((member) => member.kind));
	return KIND_ORDER.filter((kind) => present.has(kind));
}

export function refModBadge(info: RefModInfo | undefined): string {
	const kinds = refModKinds(info);
	if (kinds.length === 0) return REFMOD_FALLBACK_BADGE;
	return kinds.map((kind) => KIND_BADGES[kind]).join(' + ');
}

export function refModName(modelRef: string, directory: RefModDirectory): string {
	const id = refModId(modelRef);
	return directory[id]?.name || id;
}

export function refModMemberKinds(info: RefModInfo | undefined): PromptResourceKind[] {
	const members = info?.members ?? [];
	return members.length > 0 ? members.map((member) => member.kind) : ['image'];
}

export function refModCopies(row: unknown): number {
	const raw = row && typeof row === 'object' ? (row as Record<string, unknown>).copies : undefined;
	const value = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() ? Number(raw) : 1;
	return Number.isInteger(value) && value >= 1 ? value : 1;
}

export function refModPrimaryKind(info: RefModInfo | undefined): PromptResourceKind {
	return refModKinds(info)[0] ?? 'image';
}
