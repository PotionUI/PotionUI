export const COLUMN_WIDTH = 384;
export const COLUMN_MIN = 320;
export const COLUMN_MAX = 768;
export const COLUMN_STEP = 16;
export const COLUMN_BIG_STEP = 64;

export type ColumnWidths = Array<number | null>;

export interface WidthStorage {
	getItem(key: string): string | null;
	setItem(key: string, value: string): void;
	removeItem(key: string): void;
}

export function clampWidth(value: unknown): number {
	const number = Number(value);
	if (!Number.isFinite(number)) return COLUMN_WIDTH;
	return Math.round(Math.min(COLUMN_MAX, Math.max(COLUMN_MIN, number)));
}

export function keyedWidth(width: number, key: string, shift = false): number | null {
	const step = shift ? COLUMN_BIG_STEP : COLUMN_STEP;
	if (key === 'ArrowLeft') return clampWidth(width - step);
	if (key === 'ArrowRight') return clampWidth(width + step);
	if (key === 'Home') return COLUMN_WIDTH;
	return null;
}

export function widthsKey(elixirId: string): string {
	return `elixirs:column-widths:${elixirId}`;
}

export function columnWidth(widths: ReadonlyArray<number | null | undefined> | null | undefined, index: number): number {
	const value = widths?.[index];
	return value === null || value === undefined ? COLUMN_WIDTH : clampWidth(value);
}

export function withWidth(widths: ColumnWidths | null | undefined, index: number, width: number): ColumnWidths {
	const next: ColumnWidths = [...(widths ?? [])];
	while (next.length <= index) next.push(null);
	const clamped = clampWidth(width);
	next[index] = clamped === COLUMN_WIDTH ? null : clamped;
	while (next.length && next[next.length - 1] === null) next.pop();
	return next;
}

export function readWidths(storage: Pick<WidthStorage, 'getItem'> | null | undefined, elixirId: string): ColumnWidths {
	try {
		const raw = storage?.getItem(widthsKey(elixirId));
		const parsed: unknown = raw ? JSON.parse(raw) : [];
		return Array.isArray(parsed) ? parsed.map((value) => (typeof value === 'number' ? clampWidth(value) : null)) : [];
	} catch {
		return [];
	}
}

export function writeWidths(storage: Pick<WidthStorage, 'setItem' | 'removeItem'> | null | undefined, elixirId: string, widths: ReadonlyArray<number | null | undefined>): void {
	try {
		if (!storage) return;
		if (!widths.some((value) => value !== null && value !== undefined)) storage.removeItem(widthsKey(elixirId));
		else storage.setItem(widthsKey(elixirId), JSON.stringify(widths));
	} catch {
		return;
	}
}

export function viewerStorage(): Storage | null {
	try {
		return typeof window !== 'undefined' ? window.localStorage : null;
	} catch {
		return null;
	}
}
