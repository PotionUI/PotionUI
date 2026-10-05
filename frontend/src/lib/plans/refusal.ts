import type { LimitFormat, LimitRefusal, LimitRow } from './meApi';
import { formatCountdown, msUntil } from './countdown';
import { formatSize } from './format';

export const UPLOAD_FILE_SIZE_KIND = 'upload_file_size';
export const UPLOAD_FILE_SIZE_CODE = 'upload_file_size_exceeded';

export function isPerItemRefusal(refusal: Pick<LimitRefusal, 'kind' | 'code'>): boolean {
	return refusal.code === UPLOAD_FILE_SIZE_CODE || refusal.kind === UPLOAD_FILE_SIZE_KIND;
}

export function isPerItemError(error: unknown): boolean {
	const refusal = refusalFromError(error);
	return refusal !== null && isPerItemRefusal(refusal);
}

export function uploadSizeMessage(fileBytes: number, limitBytes: number, contactLine?: string | null): string {
	const base = `This file is ${formatSize(fileBytes)}; your plan allows files up to ${formatSize(limitBytes)}.`;
	return contactLine ? `${base} ${contactLine}` : base;
}

export const DEFAULT_CONTACT_LINE = 'Ask your admin for more.';

export interface LimitGate {
	kind: string;
	title: string;
	detail: string;
	reason: string;
	resetsAt: string | null;
	canFreeUp: boolean;
	remainingMs: number | null;
}

interface GateSource {
	kind: string;
	label: string;
	format: LimitFormat;
	resets_at: string | null;
	contact_line?: string;
}

function asBody(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}

export function parseLimitRefusal(data: unknown): LimitRefusal | null {
	const body = asBody(data);
	if (!body) return null;
	const inner = asBody(body.detail) ?? body;
	if (inner.error !== 'limit_exceeded' || typeof inner.kind !== 'string') return null;
	return inner as unknown as LimitRefusal;
}

export function refusalFromError(error: unknown): LimitRefusal | null {
	const attached = (error as { limitRefusal?: LimitRefusal } | null)?.limitRefusal;
	if (attached) return attached;
	return parseLimitRefusal((error as { response?: { data?: unknown } } | null)?.response?.data);
}

export function limitRefusalError(refusal: LimitRefusal, fallback: string): Error {
	const error = new Error(fallback) as Error & { limitRefusal?: LimitRefusal };
	error.limitRefusal = refusal;
	return error;
}

function titleFor(source: GateSource, ms: number | null): string {
	if (source.format === 'percent') return 'Cloud budget used up';
	if (ms !== null) return ms <= 36 * 3600 * 1000 ? 'Daily limit reached' : `${source.label} limit reached`;
	return `${source.label} is full`;
}

export function describeGate(source: GateSource, now: number): LimitGate | null {
	const ms = msUntil(source.resets_at, now);
	if (source.resets_at && ms === 0) return null;
	const title = titleFor(source, ms);
	const contact = source.contact_line || DEFAULT_CONTACT_LINE;
	const canFreeUp = ms === null && source.format === 'bytes';
	const waitText = ms !== null ? `resets in ${formatCountdown(ms)}` : null;
	const detail = canFreeUp
		? `Delete some generations or uploads to make room. ${contact}`
		: ms !== null
			? `You can try again in ${formatCountdown(ms)}. ${contact}`
			: contact;
	const reason = waitText ? `${title}, ${waitText}. ${contact}` : `${title}. ${contact}`;
	return {
		kind: source.kind,
		title,
		detail,
		reason,
		resetsAt: source.resets_at,
		canFreeUp,
		remainingMs: ms
	};
}

export function gateFromRefusal(refusal: LimitRefusal, now: number): LimitGate | null {
	return describeGate(
		{
			kind: refusal.kind,
			label: refusal.label ?? refusal.kind,
			format: refusal.format ?? (refusal.resets_at ? 'count' : 'bytes'),
			resets_at: refusal.resets_at ?? null,
			contact_line: refusal.contact_line
		},
		now
	);
}

export function gateFromRow(row: LimitRow, now: number, contactLine?: string): LimitGate | null {
	return describeGate({ ...row, contact_line: contactLine }, now);
}

export function blocksSubmit(row: LimitRow): boolean {
	if (row.state !== 'full' || !row.enforced || row.format === 'percent') return false;
	return row.enforce_at ? row.enforce_at.includes('submit') : true;
}

export function refusalMessage(error: unknown, now: number = Date.now()): string | null {
	const refusal = refusalFromError(error);
	if (!refusal) return null;
	if (isPerItemRefusal(refusal)) {
		if (refusal.message) return refusal.message;
		const size = refusal.incoming ?? refusal.used;
		return typeof size === 'number' && typeof refusal.limit === 'number'
			? uploadSizeMessage(size, refusal.limit, refusal.contact_line)
			: 'This file is larger than your plan allows.';
	}
	const gate = gateFromRefusal(refusal, now);
	if (!gate) return refusal.message ?? 'A limit on your account was reached.';
	return gate.canFreeUp ? `${gate.title}. Free up space in History or Library, then try again.` : gate.reason;
}
