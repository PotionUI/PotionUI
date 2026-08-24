import type { FormValidationErrorResponse } from '$lib/types/api';

type ErrorBody = Record<string, unknown>;

function asErrorBody(value: unknown): ErrorBody | null {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as ErrorBody) : null;
}

/**
 * The application normally returns error bodies directly, while FastAPI wraps
 * an equivalent body as `{ detail: { ... } }`. Keep response-shape handling at
 * this boundary so every classifier below sees the same contract.
 */
export function normalizeGenerationErrorBody(data: unknown): ErrorBody | null {
	const body = asErrorBody(data);
	if (!body) return null;
	return asErrorBody(body.detail) ?? body;
}

/**
 * Narrows an arbitrary axios error-response body to the backend's 422
 * `form_validation_failed` shape (`POST /api/generations/start`). Deliberately
 * defensive about `field_errors` — the backend contract is still settling, so
 * this must not throw if the key is absent or malformed.
 */
export function isFormValidationErrorResponse(data: unknown): data is FormValidationErrorResponse {
	const candidate = asErrorBody(data);
	if (!candidate) return false;
	return candidate.error === 'form_validation_failed' && isFieldErrorsShape(candidate.field_errors);
}

function isFieldErrorsShape(value: unknown): value is Record<string, string[]> {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
	return Object.values(value as Record<string, unknown>).every(
		(messages) => Array.isArray(messages) && messages.every((m) => typeof m === 'string')
	);
}

export type GenerationStartFailureKind = 'field_validation' | 'other';

export interface GenerationStartFailure {
	kind: GenerationStartFailureKind;
	/** Per-field messages. Always `{}` for `kind: 'other'`. */
	fieldErrors: Record<string, string[]>;
	/** User-facing summary — shown as a toast for `kind: 'other'`, ignored for
	 *  `kind: 'field_validation'` (the per-field messages carry the detail). */
	message: string;
}

export interface LtxGeometryValidationError {
	originalResolution: string;
	suggestedResolution: string;
	safeUpscale: '2.0x';
	message: string;
}

const LTX_GEOMETRY_RESOLUTION_FIELD = 'resolution';
const LTX_GEOMETRY_UPSCALE_FIELD = 'upscale';
const LTX_GEOMETRY_FIELDS = [LTX_GEOMETRY_RESOLUTION_FIELD, LTX_GEOMETRY_UPSCALE_FIELD];

// This intentionally matches the backend's message as a full signature. The API
// has no structured error code yet, so a looser match could incorrectly turn an
// unrelated validation_error into inline field state.
const LTX_GEOMETRY_MESSAGE =
	/^LTX 1\.5x upscale of ([1-9]\d*)x([1-9]\d*) is not achievable: the upsampled stage-1 latent lands on a [1-9]\d*x[1-9]\d* grid, but the refine stage is configured for [1-9]\d*x[1-9]\d* \(target resolution [1-9]\d*x[1-9]\d*\) -- 1\.5x only lands on a clean grid when both the width and height are multiples of [1-9]\d*px\. Nearest achievable resolution: ([1-9]\d*)x([1-9]\d*)\. You can also switch Upscale to 2\.0x, which is always achievable at any resolution\.$/;

/** Parses the message portion only for the field renderer, after classification
 * has already proved the enclosing error body is the exact validation error. */
export function parseLtxGeometryValidationMessage(message: unknown): Omit<LtxGeometryValidationError, 'message'> | null {
	if (typeof message !== 'string') return null;
	const match = LTX_GEOMETRY_MESSAGE.exec(message);
	if (!match) return null;
	return {
		originalResolution: `${match[1]}x${match[2]}`,
		suggestedResolution: `${match[3]}x${match[4]}`,
		safeUpscale: '2.0x'
	};
}

/**
 * Strictly identifies the LTX 1.5x geometry preflight. Both the backend
 * `validation_error` discriminator and the full safe-resolution message are
 * required before callers may attach the error to form fields.
 */
export function parseLtxGeometryValidationError(data: unknown): LtxGeometryValidationError | null {
	const body = normalizeGenerationErrorBody(data);
	if (!body || body.error !== 'validation_error') return null;
	const parsed = parseLtxGeometryValidationMessage(body.message);
	return parsed && typeof body.message === 'string' ? { ...parsed, message: body.message } : null;
}

/**
 * A one-click fix a field can offer for one of its own server-validation
 * messages: the value to write, how to label the offer, and every field whose
 * error message that single write resolves.
 */
export interface FieldErrorFix {
	/** Field the fix writes to (always the field offering it). */
	fieldName: string;
	/** Value to write, in the field's own value format. */
	value: string;
	verb: string;
	/** The value as the user reads it — rendered in mono/tabular type. */
	valueLabel: string;
	resolvesFields: string[];
}

export function formatFieldErrorFixLabel(fix: FieldErrorFix): string {
	return `${fix.verb} ${fix.valueLabel}`;
}

/**
 * The resolution field's value format is `WxH`, which is also how the backend
 * names the nearest achievable resolution — only the separator differs between
 * the value and its typeset label.
 */
function formatResolutionLabel(resolution: string): string {
	return resolution.replace('x', '×');
}

/**
 * Derives the quick-fix a field can offer for its server-validation messages,
 * or `null` when none of them carries an applicable suggestion.
 */
export function deriveFieldErrorFix(fieldName: string, messages: string[]): FieldErrorFix | null {
	for (const message of messages) {
		const geometry = parseLtxGeometryValidationMessage(message);
		if (!geometry) continue;
		if (fieldName === LTX_GEOMETRY_RESOLUTION_FIELD) {
			return {
				fieldName,
				value: geometry.suggestedResolution,
				verb: 'Use',
				valueLabel: formatResolutionLabel(geometry.suggestedResolution),
				resolvesFields: [...LTX_GEOMETRY_FIELDS]
			};
		}
		if (fieldName === LTX_GEOMETRY_UPSCALE_FIELD) {
			return {
				fieldName,
				value: geometry.safeUpscale,
				verb: 'Switch to',
				valueLabel: geometry.safeUpscale,
				resolvesFields: [...LTX_GEOMETRY_FIELDS]
			};
		}
	}
	return null;
}

/**
 * Classifies a failed `POST /api/generations/start` call (as thrown by axios,
 * i.e. `error.response.{status,data}`) into a field-validation failure (422
 * `form_validation_failed`, feed into `DynamicForm.fieldErrors`, no toast) vs.
 * any other failure (404 form_not_found, template build errors, 500s, network
 * errors — show the generic error toast).
 */
export function classifyGenerationStartError(error: unknown): GenerationStartFailure {
	const response = (error as { response?: { status?: number; data?: unknown } } | undefined)?.response;
	const data = normalizeGenerationErrorBody(response?.data);

	if (response?.status === 422 && isFormValidationErrorResponse(data)) {
		return {
			kind: 'field_validation',
			fieldErrors: data.field_errors,
			message: typeof data.message === 'string' ? data.message : 'Some fields need attention before generating.'
		};
	}

	const ltxGeometry = response?.status === 400 ? parseLtxGeometryValidationError(data) : null;
	if (ltxGeometry) {
		return {
			kind: 'field_validation',
			fieldErrors: {
				[LTX_GEOMETRY_RESOLUTION_FIELD]: [ltxGeometry.message],
				[LTX_GEOMETRY_UPSCALE_FIELD]: [ltxGeometry.message]
			},
			message: ltxGeometry.message
		};
	}

	const message =
		(typeof data?.message === 'string' && data.message) ||
		(typeof data?.detail === 'string' && data.detail) ||
		(typeof data?.error === 'string' && data.error) ||
		(error instanceof Error && error.message) ||
		'Failed to start generation.';

	return { kind: 'other', fieldErrors: {}, message };
}

/**
 * Returns a copy of `fieldErrors` with `fieldName` removed (used to clear an
 * error as soon as the user edits the offending field). Returns the same
 * object reference when the field had no error, so callers can skip
 * re-rendering/updating stores.
 */
export function clearFieldError(
	fieldErrors: Record<string, string[]>,
	fieldName: string
): Record<string, string[]> {
	if (!(fieldName in fieldErrors)) return fieldErrors;
	const next = { ...fieldErrors };
	delete next[fieldName];
	return next;
}

/** Number of `fieldNames` that currently have at least one error message —
 *  used to render a per-tab error count badge. */
export function countMatchingFieldErrors(
	fieldErrors: Record<string, string[]>,
	fieldNames: string[]
): number {
	return fieldNames.reduce((count, name) => (fieldErrors[name]?.length ? count + 1 : count), 0);
}
