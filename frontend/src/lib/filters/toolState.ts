export interface FilterToolState<T> {
	active: T | null;
	intensity: number;
	compare: boolean;
}

export const FULL_INTENSITY = 100;

export function initialToolState<T>(): FilterToolState<T> {
	return { active: null, intensity: FULL_INTENSITY, compare: false };
}

export function clampIntensity(value: number): number {
	if (!Number.isFinite(value)) return FULL_INTENSITY;
	return Math.min(100, Math.max(0, Math.round(value)));
}

export function selectActive<T>(
	state: FilterToolState<T>,
	active: T | null,
	defaultIntensity: number = FULL_INTENSITY
): FilterToolState<T> {
	if (active === null) return initialToolState<T>();
	return { active, intensity: clampIntensity(defaultIntensity), compare: false };
}

export function withIntensity<T>(state: FilterToolState<T>, value: number): FilterToolState<T> {
	return { ...state, intensity: clampIntensity(value) };
}

export function withCompare<T>(state: FilterToolState<T>, on: boolean): FilterToolState<T> {
	if (state.active === null) return { ...state, compare: false };
	return { ...state, compare: on };
}

export function previewIntensity<T>(state: FilterToolState<T>): number {
	if (state.active === null || state.compare) return 0;
	return state.intensity;
}

export function canApply<T>(state: FilterToolState<T>): boolean {
	return state.active !== null && state.intensity > 0;
}

export function historyLabel(name: string, intensity: number): string {
	return `Filter: ${name} ${clampIntensity(intensity)}%`;
}
