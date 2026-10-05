import type { LimitRow } from '$lib/plans/meApi';

export const DAILY_GENERATIONS_KIND = 'generations_per_day';

export type DailyShortfall = { needed: number; remaining: number };

export function dailyRemaining(rows: LimitRow[]): number | null {
	const row = rows.find((entry) => entry.kind === DAILY_GENERATIONS_KIND && entry.enforced);
	if (!row) return null;
	return Math.max(0, row.limit - row.used);
}

export function dailyShortfall(rows: LimitRow[], needed: number): DailyShortfall | null {
	if (needed <= 0) return null;
	const remaining = dailyRemaining(rows);
	if (remaining === null || needed <= remaining) return null;
	return { needed, remaining };
}

export function shortfallFromError(error: unknown): DailyShortfall | null {
	const body = (error as { response?: { data?: unknown } } | null)?.response?.data;
	const root = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
	if (!root) return null;
	const inner =
		root.detail && typeof root.detail === 'object'
			? (root.detail as Record<string, unknown>)
			: root.data && typeof root.data === 'object'
				? (root.data as Record<string, unknown>)
				: root;
	const needed = Number(inner.needed);
	const remaining = Number(inner.remaining);
	if (!Number.isFinite(needed) || !Number.isFinite(remaining)) return null;
	return { needed, remaining };
}

export function shortfallMessage(shortfall: DailyShortfall, contactLine: string): string {
	return `${shortfall.needed} needed, ${shortfall.remaining} left today. ${contactLine}`;
}

export function formatEstimate(cells: number, perCellMs: number | null): string {
	if (!perCellMs || perCellMs <= 0 || cells <= 0) return '-';
	const totalSeconds = (cells * perCellMs) / 1000;
	if (totalSeconds < 45) return `about ${Math.max(1, Math.round(totalSeconds))} s`;
	const minutes = totalSeconds / 60;
	if (minutes < 90) return `about ${Math.max(1, Math.round(minutes))} min`;
	const hours = minutes / 60;
	return `about ${Math.round(hours * 10) / 10} h`;
}
