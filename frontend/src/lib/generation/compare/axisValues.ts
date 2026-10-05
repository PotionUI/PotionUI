import {
	MAX_AXIS_VALUES,
	PROMPT_AXIS_FIELD,
	type AxisOption,
	type CompareAxis,
	type CompareAxisValue,
	type CompareConfig
} from './types';

export function effectiveAxes(config: Pick<CompareConfig, 'x' | 'y'>): {
	cols: CompareAxis | null;
	rows: CompareAxis | null;
} {
	const x = config.x && config.x.values.length > 0 ? config.x : null;
	const y = config.y && config.y.values.length > 0 ? config.y : null;
	if (!x && y) return { cols: y, rows: null };
	return { cols: x, rows: y };
}

export function cellCount(config: Pick<CompareConfig, 'x' | 'y'>): number {
	const { cols, rows } = effectiveAxes(config);
	if (!cols) return 0;
	return cols.values.length * (rows ? rows.values.length : 1);
}

export function compareHasContent(config: Pick<CompareConfig, 'armed' | 'x' | 'y'>): boolean {
	return config.armed || (config.x?.values.length ?? 0) > 0 || (config.y?.values.length ?? 0) > 0;
}

export function formatCellSummary(config: Pick<CompareConfig, 'x' | 'y'>): string {
	const { cols, rows } = effectiveAxes(config);
	if (!cols) return '';
	if (!rows) return `${cols.values.length} generations`;
	return `${cols.values.length} × ${rows.values.length} = ${cols.values.length * rows.values.length} generations`;
}

function decimalsOf(n: number): number {
	if (!Number.isFinite(n)) return 0;
	const text = String(n);
	if (text.includes('e-')) return Number(text.split('e-')[1]);
	const dot = text.indexOf('.');
	return dot === -1 ? 0 : text.length - dot - 1;
}

export function clampNumber(value: number, min: number | null, max: number | null): number {
	let next = value;
	if (min !== null && next < min) next = min;
	if (max !== null && next > max) next = max;
	return next;
}

export function formatNumber(value: number, step?: number | null): string {
	const places = Math.min(6, Math.max(decimalsOf(value), step ? decimalsOf(step) : 0));
	const fixed = value.toFixed(places);
	return places > 0 ? String(Number(fixed)) : fixed;
}

export function rangeValues(
	from: number,
	to: number,
	step: number,
	min: number | null = null,
	max: number | null = null,
	limit: number = MAX_AXIS_VALUES
): number[] {
	if (![from, to, step].every(Number.isFinite) || step === 0) return [];
	const places = Math.max(decimalsOf(from), decimalsOf(to), decimalsOf(step));
	const direction = to >= from ? 1 : -1;
	const stride = Math.abs(step) * direction;
	const out: number[] = [];
	const total = Math.floor(Math.abs(to - from) / Math.abs(step) + 1e-9) + 1;
	for (let i = 0; i < total && out.length < limit; i++) {
		const raw = Number((from + stride * i).toFixed(places));
		const clamped = clampNumber(raw, min, max);
		if (!out.includes(clamped)) out.push(clamped);
	}
	return out;
}

export function parseNumberList(
	text: string,
	min: number | null = null,
	max: number | null = null,
	limit: number = MAX_AXIS_VALUES
): number[] {
	const out: number[] = [];
	for (const token of text.split(/[\s,;]+/)) {
		if (!token) continue;
		const parsed = Number(token);
		if (!Number.isFinite(parsed)) continue;
		const clamped = clampNumber(parsed, min, max);
		if (!out.includes(clamped)) out.push(clamped);
		if (out.length >= limit) break;
	}
	return out;
}

export function numberAxisValues(values: number[], step: number | null = null): CompareAxisValue[] {
	return values.map((value) => ({ value, label: formatNumber(value, step) }));
}

export const MAX_SEED = 4294967295;

export function randomSeeds(count: number, rng: () => number = Math.random): number[] {
	const target = Math.max(0, Math.min(count, MAX_AXIS_VALUES));
	const seen = new Set<number>();
	while (seen.size < target) seen.add(Math.floor(rng() * MAX_SEED));
	return [...seen];
}

export function seedAxisValues(seeds: number[]): CompareAxisValue[] {
	return seeds.map((seed) => ({ value: seed, label: String(seed) }));
}

export function sameValue(a: unknown, b: unknown): boolean {
	return a === b || JSON.stringify(a) === JSON.stringify(b);
}

export function chipOptionsFor(type: string, options: AxisOption[]): AxisOption[] {
	if (type !== 'checkbox_group') return options;
	return options.map((option) => ({ value: [option.value], label: option.label }));
}

export function isChipSelected(current: CompareAxisValue[], value: unknown): boolean {
	return current.some((entry) => sameValue(entry.value, value));
}

export function chipsAxisValues(options: AxisOption[], selected: unknown[]): CompareAxisValue[] {
	return options
		.filter((option) => selected.some((value) => sameValue(value, option.value)))
		.map((o) => ({ value: o.value, label: o.label }));
}

export function toggleChip(current: CompareAxisValue[], options: AxisOption[], value: unknown): CompareAxisValue[] {
	const next = isChipSelected(current, value)
		? current.filter((entry) => !sameValue(entry.value, value))
		: [...current, options.find((option) => sameValue(option.value, value)) ?? { value, label: String(value) }];
	const order = (entry: CompareAxisValue) => options.findIndex((option) => sameValue(option.value, entry.value));
	return next.slice().sort((a, b) => order(a) - order(b));
}

export function allChips(options: AxisOption[]): CompareAxisValue[] {
	return options.slice(0, MAX_AXIS_VALUES).map((option) => ({ value: option.value, label: option.label }));
}

export function checkboxAxisValues(): CompareAxisValue[] {
	return [
		{ value: true, label: 'on' },
		{ value: false, label: 'off' }
	];
}

export function countMatches(text: string, find: string): number {
	if (!find) return 0;
	let count = 0;
	let at = text.indexOf(find);
	while (at !== -1) {
		count += 1;
		at = text.indexOf(find, at + find.length);
	}
	return count;
}

export function promptAxisValues(find: string, replacements: Array<string | null>): CompareAxisValue[] {
	const seen = new Set<string>();
	const out: CompareAxisValue[] = [];
	for (const replacement of replacements) {
		const key = replacement === null ? '\u0000keep' : replacement;
		if (seen.has(key)) continue;
		seen.add(key);
		out.push({
			value: { find, replace: replacement },
			label: replacement === null ? find : replacement
		});
	}
	return out.slice(0, MAX_AXIS_VALUES);
}

export function applyPromptReplacement(text: string, value: unknown): string {
	if (!value || typeof value !== 'object') return text;
	const { find, replace } = value as { find?: string; replace?: string | null };
	if (!find || replace === null || replace === undefined) return text;
	return text.split(find).join(replace);
}

export function loraAxisValues(
	lora: unknown,
	loraName: string,
	strengths: number[],
	step: number | null = null
): CompareAxisValue[] {
	return strengths.map((strength) => ({
		value: { lora, strength },
		label: `${loraName} · ${formatNumber(strength, step)}`
	}));
}

export function isPromptAxis(axis: Pick<CompareAxis, 'field'> | null | undefined): boolean {
	return axis?.field === PROMPT_AXIS_FIELD;
}

export function valueLabelFor(axis: CompareAxis | null, value: unknown): string {
	const hit = axis?.values.find((entry) => JSON.stringify(entry.value) === JSON.stringify(value));
	return hit?.label ?? String(value);
}

export function axisFormValue(axis: CompareAxis | null): string {
	if (!axis || axis.values.length === 0) return '';
	return axis.values[0].label;
}
