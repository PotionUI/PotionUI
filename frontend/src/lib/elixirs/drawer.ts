import type { WidthStorage } from './columns';

export const DRAWER_WIDTH = 640;
export const DRAWER_MIN = 416;
export const DRAWER_MAX_SHARE = 0.65;
export const DRAWER_STEP = 16;
export const DRAWER_BIG_STEP = 64;
export const DRAWER_KEY = 'elixirs:drawer-width';

export function drawerMax(tabWidth: number | null | undefined): number {
	const width = Number(tabWidth);
	if (!Number.isFinite(width) || width <= 0) return Number.POSITIVE_INFINITY;
	return Math.max(DRAWER_MIN, Math.floor(width * DRAWER_MAX_SHARE));
}

export function clampDrawer(value: unknown, tabWidth?: number | null): number {
	const number = Number(value);
	const wanted = Number.isFinite(number) ? number : DRAWER_WIDTH;
	return Math.round(Math.min(drawerMax(tabWidth), Math.max(DRAWER_MIN, wanted)));
}

export function draggedDrawer(start: number, startX: number, x: number, tabWidth?: number | null): number {
	return clampDrawer(start + startX - x, tabWidth);
}

export function keyedDrawer(width: number, key: string, shift = false, tabWidth?: number | null): number | null {
	const step = shift ? DRAWER_BIG_STEP : DRAWER_STEP;
	if (key === 'ArrowLeft') return clampDrawer(width + step, tabWidth);
	if (key === 'ArrowRight') return clampDrawer(width - step, tabWidth);
	if (key === 'Home') return clampDrawer(DRAWER_WIDTH, tabWidth);
	return null;
}

export function readDrawer(storage: Pick<WidthStorage, 'getItem'> | null | undefined): number {
	try {
		const raw = storage?.getItem(DRAWER_KEY);
		return raw ? clampDrawer(raw) : DRAWER_WIDTH;
	} catch {
		return DRAWER_WIDTH;
	}
}

export function writeDrawer(storage: Pick<WidthStorage, 'setItem' | 'removeItem'> | null | undefined, width: number): void {
	try {
		if (!storage) return;
		const clamped = clampDrawer(width);
		if (clamped === DRAWER_WIDTH) storage.removeItem(DRAWER_KEY);
		else storage.setItem(DRAWER_KEY, String(clamped));
	} catch {
		return;
	}
}
