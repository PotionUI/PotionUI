import type { LimitKindDescriptor, Plan, PlanBody } from './types';

export type ByteUnit = 'GB' | 'TB';

export const BYTE_UNIT_OPTIONS: ReadonlyArray<{ value: ByteUnit; label: string }> = [
	{ value: 'GB', label: 'GB' },
	{ value: 'TB', label: 'TB' }
];

const BYTE_FACTORS: Record<ByteUnit, number> = { GB: 1024 ** 3, TB: 1024 ** 4 };

export interface DraftLimit {
	kind: string;
	text: string;
	unit: ByteUnit;
}

export interface PlanDraft {
	name: string;
	description: string;
	limits: DraftLimit[];
}

export function bytesToDisplay(bytes: number): { text: string; unit: ByteUnit } {
	const unit: ByteUnit = bytes >= BYTE_FACTORS.TB && bytes % BYTE_FACTORS.TB === 0 ? 'TB' : 'GB';
	const value = bytes / BYTE_FACTORS[unit];
	return { text: String(Number(value.toFixed(3))), unit };
}

export function displayToValue(kind: Pick<LimitKindDescriptor, 'value_type'>, text: string, unit: ByteUnit): number | null {
	const parsed = Number(text.trim());
	if (text.trim() === '' || !Number.isFinite(parsed) || parsed < 0) return null;
	if (kind.value_type === 'bytes') return Math.round(parsed * BYTE_FACTORS[unit]);
	if (kind.value_type === 'count') return Number.isInteger(parsed) ? parsed : null;
	return Math.round(parsed * 100) / 100;
}

export function valueToDisplay(kind: Pick<LimitKindDescriptor, 'value_type'>, value: number): { text: string; unit: ByteUnit } {
	if (kind.value_type === 'bytes') return bytesToDisplay(value);
	return { text: String(value), unit: 'GB' };
}

export function emptyDraft(): PlanDraft {
	return { name: '', description: '', limits: [] };
}

export function draftFromPlan(plan: Plan, kinds: readonly LimitKindDescriptor[]): PlanDraft {
	return {
		name: plan.name,
		description: plan.description ?? '',
		limits: plan.limits.map((limit) => {
			const kind = kinds.find((k) => k.key === limit.kind);
			const { text, unit } = kind ? valueToDisplay(kind, limit.value) : { text: String(limit.value), unit: 'GB' as ByteUnit };
			return { kind: limit.kind, text, unit };
		})
	};
}

export function availableKinds(kinds: readonly LimitKindDescriptor[], draft: PlanDraft): LimitKindDescriptor[] {
	const used = new Set(draft.limits.map((limit) => limit.kind));
	return kinds.filter((kind) => kind.active !== false && !used.has(kind.key));
}

export function addLimit(draft: PlanDraft, kind: LimitKindDescriptor): PlanDraft {
	if (draft.limits.some((limit) => limit.kind === kind.key)) return draft;
	return { ...draft, limits: [...draft.limits, { kind: kind.key, text: '', unit: 'GB' }] };
}

export function removeLimit(draft: PlanDraft, kindKey: string): PlanDraft {
	return { ...draft, limits: draft.limits.filter((limit) => limit.kind !== kindKey) };
}

export function setLimitText(draft: PlanDraft, kindKey: string, text: string): PlanDraft {
	return { ...draft, limits: draft.limits.map((limit) => (limit.kind === kindKey ? { ...limit, text } : limit)) };
}

export function setLimitUnit(draft: PlanDraft, kindKey: string, unit: ByteUnit): PlanDraft {
	return { ...draft, limits: draft.limits.map((limit) => (limit.kind === kindKey ? { ...limit, unit } : limit)) };
}

export function draftToBody(draft: PlanDraft, kinds: readonly LimitKindDescriptor[]): PlanBody | null {
	const limits = [];
	for (const limit of draft.limits) {
		const kind = kinds.find((k) => k.key === limit.kind);
		if (!kind) return null;
		const value = displayToValue(kind, limit.text, limit.unit);
		if (value === null) return null;
		limits.push({ kind: limit.kind, value });
	}
	return { name: draft.name.trim(), description: draft.description.trim(), limits };
}

export function isDraftValid(draft: PlanDraft, kinds: readonly LimitKindDescriptor[]): boolean {
	return draft.name.trim().length > 0 && draftToBody(draft, kinds) !== null;
}

export function isDraftDirty(draft: PlanDraft, snapshot: PlanDraft): boolean {
	return JSON.stringify(draft) !== JSON.stringify(snapshot);
}
