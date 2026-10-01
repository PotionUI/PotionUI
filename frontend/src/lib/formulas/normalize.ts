import type { PlanChange, PlanSame, PlanSkip, ServerPlan } from './types';

const LIBRARY_CODES = new Set(['lora_unavailable', 'model_unavailable']);

type Raw = Record<string, any>;

function skipRow(detail: unknown): string | undefined {
	const model = (detail as { model?: unknown } | null)?.model;
	return typeof model === 'string' ? model : undefined;
}

export function normalizePlan(raw: Raw | null | undefined): ServerPlan {
	const changes: PlanChange[] = (raw?.changes ?? []).map((item: Raw) => ({
		field: item.name,
		label: item.label ?? item.name,
		group: item.group_id,
		groupLabel: item.group_label ?? item.group_id,
		old: item.old,
		new: item.new,
		advanced: !!item.advanced,
		type: item.type ?? undefined,
		companionOf: item.companion_of ?? undefined,
		rows: Array.isArray(item.rows) ? item.rows : undefined
	}));
	const same: PlanSame[] = (raw?.same ?? []).map((item: Raw) => ({
		field: item.name,
		label: item.label ?? item.name,
		group: item.group_id
	}));
	const skips: PlanSkip[] = (raw?.skips ?? []).map((item: Raw) => ({
		field: item.name,
		label: item.label ?? item.name,
		group: item.group_id,
		code: item.code ?? '',
		reason: item.reason ?? 'This setting cannot be applied.',
		row: skipRow(item.detail),
		library: LIBRARY_CODES.has(item.code)
	}));
	return { changes, same, skips };
}
