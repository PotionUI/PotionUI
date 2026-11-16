import { describeEditFailure } from '$lib/media/editors/editErrors';
import { refusalMessage } from '$lib/plans/refusal';

export interface MediaToolFailure {
	code: string | null;
	message: string;
	field: string | null;
	stepIndex: number | null;
}

const FALLBACK = 'The edit could not be applied';

const CODE_MESSAGES: Record<string, string> = {
	unknown_tool: 'This tool is no longer available.',
	unsupported_media: 'This tool does not work on this kind of media.',
	invalid_selection: 'This tool does not work with this many items.',
	replace_not_allowed: 'This edit can only be saved as a new item.'
};

function record(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string | null {
	return typeof value === 'string' && value.trim() ? value : null;
}

export function parseMediaToolFailure(error: unknown, fallback = FALLBACK): MediaToolFailure {
	const response = record(record(error)?.response);
	const data = record(response?.data);
	const body = record(data?.detail) ?? data;
	const code = text(body?.error) ?? text(body?.code);
	const step = body?.step_index;

	const message =
		refusalMessage(error) ??
		text(body?.message) ??
		(code ? CODE_MESSAGES[code] : undefined) ??
		describeEditFailure(error, fallback);

	return {
		code,
		message,
		field: text(body?.field),
		stepIndex: typeof step === 'number' && Number.isFinite(step) ? step : null
	};
}

export function describeMediaToolFailure(error: unknown, fallback = FALLBACK): string {
	return parseMediaToolFailure(error, fallback).message;
}
