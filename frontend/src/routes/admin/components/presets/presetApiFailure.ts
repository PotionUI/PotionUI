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

export const API_FIELD_KEYS = ['prefix', 'suffix', 'negative', 'modes', 'name', 'description', 'cover', 'offered', 'first'] as const;

const FIELD_KEY_ALIASES: Record<string, string> = { offered: 'modes', first: 'modes' };

export interface KeyedFieldError {
	key: string | null;
	text: string;
}

function capitalized(text: string): string {
	return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

export function splitFieldError(error: string): KeyedFieldError {
	const match = /^\s*([A-Za-z_]+)\s*:\s*([\s\S]*)$/.exec(error);
	const key = match?.[1].toLowerCase();
	if (!match || !key || !(API_FIELD_KEYS as readonly string[]).includes(key)) return { key: null, text: error };
	const text = match[2].trim();
	return { key, text: text ? capitalized(text) : error };
}

export function cleanFieldErrors(errors: readonly string[]): string {
	return errors.map((error) => splitFieldError(error).text).join(' ');
}

export interface AssignedErrors<F extends string> {
	byField: Partial<Record<F, string>>;
	general: string[];
}

function fieldForKey<F extends string>(key: string | null, fields: readonly F[]): F | undefined {
	if (!key) return undefined;
	return fields.find((name) => name === key) ?? fields.find((name) => name === FIELD_KEY_ALIASES[key]);
}

function fieldNamedIn<F extends string>(text: string, fields: readonly F[]): F | undefined {
	const lower = text.toLowerCase();
	return fields.find((name) => new RegExp(`\\b${name.toLowerCase().replace(/s$/, '')}s?\\b`).test(lower));
}

export function assignFieldErrors<F extends string>(errors: readonly string[], fields: readonly F[]): AssignedErrors<F> {
	const byField: Partial<Record<F, string>> = {};
	const general: string[] = [];
	for (const error of errors) {
		const { key, text } = splitFieldError(error);
		const field = key ? fieldForKey(key, fields) : fieldNamedIn(error, fields);
		if (!field) {
			general.push(text);
			continue;
		}
		byField[field] = byField[field] ? `${byField[field]} ${text}` : text;
	}
	return { byField, general };
}
