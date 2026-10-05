import type { LimitRow } from './meApi';
import { formatResetsIn, msUntil } from './countdown';

export function ratioOf(row: Pick<LimitRow, 'used' | 'limit'> & { percent?: number | null }): number {
	if (typeof row.percent === 'number') return row.percent / 100;
	return row.limit > 0 ? row.used / row.limit : 0;
}

export function percentOf(row: Pick<LimitRow, 'used' | 'limit'> & { percent?: number | null }): number {
	return Math.round(Math.min(1, ratioOf(row)) * 100);
}

function trim(value: number): string {
	return (Math.round(value * 10) / 10).toFixed(1).replace(/\.0$/, '');
}

function bytesIn(value: number, unit: 'GB' | 'MB'): string {
	return trim(value / (unit === 'GB' ? 2 ** 30 : 2 ** 20));
}

export function formatAmount(row: Pick<LimitRow, 'used' | 'limit' | 'format'> & { percent?: number | null }): string {
	if (row.format === 'percent') return `${Math.round(ratioOf(row) * 100)}%`;
	if (row.format === 'bytes') {
		const unit = row.limit >= 2 ** 30 ? 'GB' : 'MB';
		return `${bytesIn(row.used, unit)} / ${bytesIn(row.limit, unit)} ${unit}`;
	}
	return `${row.used} / ${row.limit}`;
}

export function resetNote(row: Pick<LimitRow, 'resets_at'>, now: number): string | null {
	const ms = msUntil(row.resets_at, now);
	if (ms === null || !row.resets_at) return null;
	return formatResetsIn(ms, row.resets_at);
}

export function leftNote(row: LimitRow): string | null {
	if (row.format !== 'bytes' || row.state === 'full') return null;
	const left = Math.max(0, row.limit - row.used);
	const unit = row.limit >= 2 ** 30 ? 'GB' : 'MB';
	return `${bytesIn(left, unit)} ${unit} left`;
}

export function closestLimit(rows: LimitRow[]): LimitRow | null {
	let best: LimitRow | null = null;
	for (const row of rows) {
		if (row.perItem) continue;
		if (!best || ratioOf(row) > ratioOf(best)) best = row;
	}
	return best;
}

export function barTone(state: LimitRow['state']): 'signal' | 'warning' | 'danger' {
	return state === 'full' ? 'danger' : state === 'warn' ? 'warning' : 'signal';
}

export function formatBytesPlain(bytes: number): string {
	if (bytes >= 2 ** 30) return `${trim(bytes / 2 ** 30)} GB`;
	return `${trim(bytes / 2 ** 20)} MB`;
}
