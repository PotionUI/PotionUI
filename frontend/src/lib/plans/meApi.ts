import { api } from '$lib/services/api/index';

export type LimitState = 'ok' | 'warn' | 'full';
export type LimitFormat = 'bytes' | 'count' | 'percent';

export interface LimitRow {
	kind: string;
	label: string;
	used: number;
	limit: number;
	format: LimitFormat;
	resets_at: string | null;
	state: LimitState;
	percent: number | null;
	enforced: boolean;
	enforce_at?: string[];
	perItem?: boolean;
}

export interface MyPlanMeta {
	planName: string | null;
	source: string;
	groupName: string | null;
	exempt: boolean;
	timezone: string | null;
	contactLine: string | null;
}

export interface MyLimits {
	rows: LimitRow[];
	meta: MyPlanMeta;
	storageBytes?: number | null;
}

export interface StorageGroup {
	key: string;
	label: string;
	files: number;
	bytes: number;
}

export interface LimitRefusal {
	error: 'limit_exceeded';
	kind: string;
	message?: string;
	code?: string;
	point?: string;
	incoming?: number | null;
	label?: string;
	used?: number | null;
	limit?: number | null;
	percent?: number | null;
	format?: LimitFormat;
	resets_at?: string | null;
	contact_line?: string;
}

const FORMATS: LimitFormat[] = ['bytes', 'count', 'percent'];
const STATES: LimitState[] = ['ok', 'warn', 'full'];

function toRow(raw: unknown): LimitRow | null {
	if (!raw || typeof raw !== 'object') return null;
	const r = raw as Record<string, unknown>;
	if (typeof r.kind !== 'string') return null;
	const used = Number(r.used ?? 0);
	const limit = Number(r.limit ?? 0);
	const percent = typeof r.percent === 'number' ? r.percent : null;
	const info = (r.kind_info ?? {}) as Record<string, unknown>;
	const format = FORMATS.includes(r.format as LimitFormat) ? (r.format as LimitFormat) : 'count';
	const ratio = percent !== null ? percent / 100 : limit > 0 ? used / limit : 0;
	const state = STATES.includes(r.state as LimitState)
		? (r.state as LimitState)
		: ratio >= 1
			? 'full'
			: ratio >= 0.8
				? 'warn'
				: 'ok';
	return {
		kind: r.kind,
		label:
			typeof info.short_label === 'string'
				? info.short_label
				: typeof r.label === 'string'
					? r.label
					: r.kind,
		percent,
		enforced: r.enforced !== false,
		perItem: info.per_item === true,
		used,
		limit,
		format,
		resets_at: typeof r.resets_at === 'string' ? r.resets_at : null,
		state,
		enforce_at: Array.isArray(info.enforce_at)
			? (info.enforce_at as string[])
			: Array.isArray(r.enforce_at)
				? (r.enforce_at as string[])
				: undefined
	};
}

function unwrapData(payload: unknown): Record<string, unknown> {
	const obj = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
	return obj.data && typeof obj.data === 'object' ? (obj.data as Record<string, unknown>) : obj;
}

export function normalizeLimits(payload: unknown): LimitRow[] {
	const body: unknown = Array.isArray(payload) ? { limits: payload } : unwrapData(payload);
	const list = (body as { limits?: unknown }).limits;
	if (!Array.isArray(list)) return [];
	return list.map(toRow).filter((row): row is LimitRow => row !== null);
}

export function normalizeMeta(payload: unknown): MyPlanMeta {
	const body = unwrapData(payload);
	const plan = body.plan as { name?: string } | null | undefined;
	const group = body.group as { name?: string } | null | undefined;
	return {
		planName: plan?.name ?? null,
		source: typeof body.source === 'string' ? body.source : 'none',
		groupName: group?.name ?? null,
		exempt: body.exempt === true,
		timezone: typeof body.timezone === 'string' ? body.timezone : null,
		contactLine: typeof body.contact_line === 'string' ? body.contact_line : null
	};
}

export function normalizeStorageBytes(payload: unknown): number | null {
	const usage = unwrapData(payload).usage;
	const bytes = usage && typeof usage === 'object' ? (usage as Record<string, unknown>).storage_bytes : null;
	return typeof bytes === 'number' && Number.isFinite(bytes) ? bytes : null;
}

export async function getMyLimits(): Promise<MyLimits> {
	const response = await api.getClient().get('/api/me/limits');
	return { rows: normalizeLimits(response.data), meta: normalizeMeta(response.data),
		storageBytes: normalizeStorageBytes(response.data)
	};
}

export async function getMyStorageBreakdown(): Promise<StorageGroup[]> {
	const response = await api.getClient().get('/api/me/limits/storage');
	const groups = unwrapData(response.data).groups;
	return Array.isArray(groups) ? (groups as StorageGroup[]) : [];
}
