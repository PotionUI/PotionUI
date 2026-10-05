import type { LimitKindDescriptor, Plan, PlanBody } from './types';

export type ByteUnit = 'MB' | 'GB' | 'TB';

const BYTE_FACTORS: Record<ByteUnit, number> = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 };

type ScaleKind = Pick<LimitKindDescriptor, 'value_type' | 'input_scale'> & { unit?: string };

export function byteUnitsFor(kind: { unit?: string } | undefined): readonly [ByteUnit, ByteUnit] {
	return kind?.unit === 'MB' ? ['MB', 'GB'] : ['GB', 'TB'];
}

export function byteUnitOptions(kind: { unit?: string } | undefined): { value: ByteUnit; label: string }[] {
	return byteUnitsFor(kind).map((unit) => ({ value: unit, label: unit }));
}

export interface DraftLimit {
	kind: string;
	text: string;
	unit: ByteUnit;
	inactiveValue?: number;
}

export interface PlanDraft {
	name: string;
	description: string;
	limits: DraftLimit[];
}

export function bytesToDisplay(
	bytes: number,
	units: readonly [ByteUnit, ByteUnit] = ['GB', 'TB']
): { text: string; unit: ByteUnit } {
	const [small, large] = units;
	const unit: ByteUnit = bytes >= BYTE_FACTORS[large] && bytes % BYTE_FACTORS[large] === 0 ? large : small;
	return { text: String(Number((bytes / BYTE_FACTORS[unit]).toFixed(3))), unit };
}

export function displayToValue(kind: ScaleKind, text: string, unit: ByteUnit): number | null {
	const parsed = Number(text.trim());
	if (text.trim() === '' || !Number.isFinite(parsed) || parsed < 0) return null;
	if (kind.value_type === 'bytes') return Math.round(parsed * BYTE_FACTORS[unit]);
	const scaled = parsed * (kind.input_scale || 1);
	if (kind.value_type === 'count') return Number.isInteger(scaled) ? scaled : null;
	return Math.round(scaled * 100) / 100;
}

export function valueToDisplay(kind: ScaleKind, value: number): { text: string; unit: ByteUnit } {
	if (kind.value_type === 'bytes') return bytesToDisplay(value, byteUnitsFor(kind));
	return { text: String(Number((value / (kind.input_scale || 1)).toFixed(2))), unit: 'GB' };
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
			if (!kind || limit.active === false) {
				return { kind: limit.kind, text: String(limit.value), unit: 'GB' as ByteUnit, inactiveValue: limit.value };
			}
			return { kind: limit.kind, ...valueToDisplay(kind, limit.value) };
		})
	};
}

export function availableKinds(kinds: readonly LimitKindDescriptor[], draft: PlanDraft): LimitKindDescriptor[] {
	const used = new Set(draft.limits.map((limit) => limit.kind));
	return kinds.filter((kind) => !used.has(kind.key));
}

export function addLimit(draft: PlanDraft, kind: LimitKindDescriptor): PlanDraft {
	if (draft.limits.some((limit) => limit.kind === kind.key)) return draft;
	return { ...draft, limits: [...draft.limits, { kind: kind.key, text: '', unit: byteUnitsFor(kind)[0] }] };
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
	const limits: PlanBody['limits'] = [];
	for (const limit of draft.limits) {
		if (limit.inactiveValue !== undefined) {
			limits.push({ kind: limit.kind, value: limit.inactiveValue });
			continue;
		}
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
