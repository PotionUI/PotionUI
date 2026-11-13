export interface ParameterMeta {
	label?: string;
	type?: string;
	unit?: string;
}

export interface ParameterModelRef {
	name?: string | null;
	filename?: string | null;
}

export interface ParameterRow {
	key: string;
	label: string;
	raw: string;
	kind: 'long' | 'short';
	text: string;
	display: string;
	unit: string;
	unitSpaced: boolean;
	mono: boolean;
	off: boolean;
	span: 1 | 2 | 3;
}

export interface ParameterView {
	longs: ParameterRow[];
	shorts: ParameterRow[];
}

export interface ParameterViewInput {
	params: Record<string, unknown> | null | undefined;
	meta?: Record<string, ParameterMeta> | null;
	order?: string[] | null;
	models?: ParameterModelRef[] | null;
}

export interface FoldResult {
	visible: ParameterRow[];
	foldable: boolean;
	total: number;
	hidden: number;
}

export const LONG_TEXT_CHARS = 80;
export const FOLD_ABOVE = 10;
export const FOLD_SHOW = 8;
export const SPAN_TWO_CHARS = 14;
export const SPAN_THREE_CHARS = 28;
export const MIN_COLUMN_WIDTHS = [520, 760] as const;

const TEXT_TYPES = new Set(['prompt', 'textarea', 'markdown']);
const BOOL_TYPES = new Set(['checkbox', 'boolean', 'switch']);
const UNSPACED_UNITS = new Set(['%', '×', '°']);
const MAX_DECIMALS = 4;

export function humanizeKey(key: string): string {
	const spaced = String(key)
		.replace(/([a-z0-9])([A-Z])/g, (_m, a: string, b: string) => `${a} ${b.toLowerCase()}`)
		.replace(/[_\-.]+/g, ' ')
		.replace(/\s+/g, ' ')
		.trim();
	if (/^[A-Za-z0-9]{1,3}$/.test(spaced)) return spaced.toUpperCase();
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function isEmptyValue(value: unknown): boolean {
	if (value === null || value === undefined) return true;
	if (typeof value === 'string') return value.trim() === '';
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === 'object') return Object.keys(value as object).length === 0;
	return false;
}

export function rawValue(value: unknown): string {
	if (value !== null && typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

function formatNumber(value: number | string): string {
	if (typeof value === 'string') {
		const trimmed = value.trim();
		if (/^-?\d+$/.test(trimmed)) return trimmed;
		return roundDecimals(Number(trimmed));
	}
	if (Number.isInteger(value)) return String(value);
	return roundDecimals(value);
}

function roundDecimals(n: number): string {
	const factor = 10 ** MAX_DECIMALS;
	return String(Math.round(n * factor) / factor);
}

function numericValue(value: unknown): number | string | null {
	if (typeof value === 'number') return Number.isFinite(value) ? value : null;
	if (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value.trim())) return value;
	return null;
}

function isBooleanValue(value: unknown, type: string | undefined): boolean {
	if (typeof value === 'boolean') return true;
	if (typeof value === 'string' && /^(true|false)$/i.test(value.trim())) return true;
	return type !== undefined && BOOL_TYPES.has(type);
}

function isTruthy(value: unknown): boolean {
	if (typeof value === 'boolean') return value;
	if (typeof value === 'number') return value !== 0;
	return /^(true|1)$/i.test(String(value).trim());
}

function withSpan(row: ParameterRow): ParameterRow {
	if (row.kind !== 'short') return row;
	const shown = row.display + (row.unit ? (row.unitSpaced ? ' ' : '') + row.unit : '');
	const span = shown.length > SPAN_THREE_CHARS ? 3 : shown.length > SPAN_TWO_CHARS ? 2 : 1;
	return { ...row, span };
}

export function classifyParameter(
	key: string,
	value: unknown,
	meta?: Record<string, ParameterMeta> | null
): ParameterRow | null {
	const row = classifyUnwidened(key, value, meta);
	return row ? withSpan(row) : null;
}

function classifyUnwidened(
	key: string,
	value: unknown,
	meta?: Record<string, ParameterMeta> | null
): ParameterRow | null {
	if (isEmptyValue(value)) return null;
	const declared = meta?.[key] ?? {};
	const type = declared.type?.toLowerCase();
	const base = {
		key,
		label: declared.label || humanizeKey(key),
		raw: rawValue(value),
		unit: declared.unit ?? '',
		unitSpaced: !UNSPACED_UNITS.has(declared.unit ?? ''),
		text: '',
		display: '',
		mono: false,
		off: false,
		span: 1 as const
	};

	if (isBooleanValue(value, type)) {
		const on = isTruthy(value);
		return { ...base, kind: 'short', display: on ? 'On' : 'Off', unit: '', off: !on };
	}

	if (typeof value === 'object') {
		if (base.raw.length > LONG_TEXT_CHARS) {
			return { ...base, kind: 'long', text: JSON.stringify(value, null, 1), mono: true };
		}
		return { ...base, kind: 'short', display: base.raw, mono: true };
	}

	const str = String(value);
	if ((type !== undefined && TEXT_TYPES.has(type)) || str.length > LONG_TEXT_CHARS || str.includes('\n')) {
		return { ...base, kind: 'long', text: str };
	}

	const numeric = numericValue(value);
	if (numeric !== null) {
		return { ...base, kind: 'short', display: formatNumber(numeric), mono: true };
	}

	const size = /^(\d+)\s*[x×]\s*(\d+)$/i.exec(str.trim());
	if (size) {
		return { ...base, kind: 'short', display: `${size[1]} × ${size[2]}`, mono: true };
	}
	return { ...base, kind: 'short', display: str };
}

export function orderKeys(
	params: Record<string, unknown>,
	order?: string[] | null
): string[] {
	const own = Object.keys(params);
	if (!order || order.length === 0) return own;
	const seen = new Set<string>();
	const ordered: string[] = [];
	for (const key of order) {
		if (key in params && !seen.has(key)) {
			seen.add(key);
			ordered.push(key);
		}
	}
	for (const key of own) if (!seen.has(key)) ordered.push(key);
	return ordered;
}

function stripExtension(name: string): string {
	return name.toLowerCase().trim().replace(/\.[a-z0-9]{2,12}$/, '');
}

function baseName(name: string): string {
	const parts = name.split(/[\\/]/);
	return parts[parts.length - 1];
}

export function modelNameSet(models?: ParameterModelRef[] | null): Set<string> {
	const names = new Set<string>();
	for (const model of models ?? []) {
		for (const candidate of [model.name, model.filename]) {
			if (!candidate) continue;
			names.add(stripExtension(candidate));
			names.add(stripExtension(baseName(candidate)));
		}
	}
	return names;
}

export function isModelDuplicate(value: unknown, names: Set<string>): boolean {
	return typeof value === 'string' && names.size > 0 && names.has(stripExtension(value));
}

export function buildParameterView(input: ParameterViewInput): ParameterView {
	const params = input.params ?? {};
	const names = modelNameSet(input.models);
	const longs: ParameterRow[] = [];
	const shorts: ParameterRow[] = [];
	for (const key of orderKeys(params, input.order)) {
		const value = params[key];
		if (isModelDuplicate(value, names)) continue;
		const row = classifyParameter(key, value, input.meta);
		if (!row) continue;
		(row.kind === 'long' ? longs : shorts).push(row);
	}
	return { longs, shorts };
}

export function columnsForWidth(width: number): number {
	if (width >= MIN_COLUMN_WIDTHS[1]) return 4;
	if (width >= MIN_COLUMN_WIDTHS[0]) return 3;
	return 2;
}

export function foldShowCount(columns: number): number {
	return Math.ceil(FOLD_SHOW / columns) * columns;
}

export function foldShortRows(shorts: ParameterRow[], expanded: boolean, columns = 2): FoldResult {
	const foldable = shorts.length > FOLD_ABOVE;
	const visible = foldable && !expanded ? shorts.slice(0, foldShowCount(columns)) : shorts;
	return { visible, foldable, total: shorts.length, hidden: shorts.length - visible.length };
}
