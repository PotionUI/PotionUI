import { isAxiosError } from 'axios';
import { parseApiError } from '$lib/utils/apiErrors';
import type { APIResponse, PresetMirrorRef, PresetUninstallData } from '$lib/types/api';

export interface PresetApiFailure {
	status: number | null;
	code: string | null;
	message: string;
	errors: string[];
	mirrors: PresetMirrorRef[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function stringList(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim() !== '') : [];
}

function mirrorList(value: unknown): PresetMirrorRef[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((item) => {
		const record = asRecord(item);
		const id = record?.preset_id;
		if (typeof id !== 'string' || !id) return [];
		const name = typeof record?.name === 'string' && record.name ? record.name : id;
		return [{ preset_id: id, name }];
	});
}

function failureFromBody(body: unknown, status: number | null, fallback: string): PresetApiFailure {
	const record = asRecord(body);
	const detail = asRecord(record?.detail);
	const source = detail ?? record;
	const code = typeof source?.error === 'string' ? source.error : null;
	const message = typeof source?.message === 'string' && source.message ? source.message : fallback;
	return {
		status,
		code,
		message,
		errors: stringList(source?.errors),
		mirrors: mirrorList(source?.mirrors)
	};
}

export function presetResponseFailure(response: APIResponse<unknown> | null | undefined, fallback: string): PresetApiFailure {
	return failureFromBody(response, null, fallback);
}

export function presetApiFailure(error: unknown, fallback: string): PresetApiFailure {
	if (isAxiosError(error) && error.response) {
		const failure = failureFromBody(error.response.data, error.response.status, fallback);
		if (failure.message === fallback && !failure.code) {
			const parsed = parseApiError(error).message;
			if (parsed && parsed !== error.message) failure.message = parsed;
		}
		return failure;
	}
	return {
		status: null,
		code: null,
		message: error instanceof Error && error.message ? error.message : fallback,
		errors: [],
		mirrors: []
	};
}

export type UninstallOutcome =
	| { kind: 'removed'; tombstone: PresetMirrorRef | null }
	| { kind: 'has_mirrors'; mirrors: PresetMirrorRef[]; message: string }
	| { kind: 'refused'; message: string };

export async function runPresetUninstall(
	call: () => Promise<APIResponse<PresetUninstallData>>,
	fallback: string
): Promise<UninstallOutcome> {
	try {
		const response = await call();
		if (!response?.success) {
			const failure = presetResponseFailure(response, fallback);
			if (failure.code === 'preset_has_mirrors') {
				return { kind: 'has_mirrors', mirrors: failure.mirrors, message: failure.message };
			}
			return { kind: 'refused', message: failure.message };
		}
		return { kind: 'removed', tombstone: response.data?.tombstone ?? null };
	} catch (error) {
		const failure = presetApiFailure(error, fallback);
		if (failure.code === 'preset_has_mirrors') {
			return { kind: 'has_mirrors', mirrors: failure.mirrors, message: failure.message };
		}
		return { kind: 'refused', message: failure.message };
	}
}

export interface AssignedErrors<F extends string> {
	byField: Partial<Record<F, string>>;
	general: string[];
}

export function assignFieldErrors<F extends string>(errors: readonly string[], fields: readonly F[]): AssignedErrors<F> {
	const byField: Partial<Record<F, string>> = {};
	const general: string[] = [];
	for (const error of errors) {
		const lower = error.toLowerCase();
		const field = fields.find((name) => new RegExp(`\\b${name.toLowerCase().replace(/s$/, '')}s?\\b`).test(lower));
		if (!field) {
			general.push(error);
			continue;
		}
		byField[field] = byField[field] ? `${byField[field]} ${error}` : error;
	}
	return { byField, general };
}
