import { describe, expect, it } from 'vitest';
import {
	classifyGenerationStartError,
	clearFieldError,
	countMatchingFieldErrors,
	deriveFieldErrorFix,
	formatFieldErrorFixLabel,
	isFormValidationErrorResponse,
	normalizeGenerationErrorBody,
	parseLtxGeometryValidationError,
	parseLtxGeometryValidationMessage
} from './formValidationErrors';

function ltxGeometryMessage(original: string, suggested: string): string {
	return `LTX 1.5x upscale of ${original} is not achievable: the upsampled stage-1 latent lands on a 45x25 grid, but the refine stage is configured for 45x26 (target resolution 1440x816) -- 1.5x only lands on a clean grid when both the width and height are multiples of 64px. Nearest achievable resolution: ${suggested}. You can also switch Upscale to 2.0x, which is always achievable at any resolution.`;
}

describe('isFormValidationErrorResponse', () => {
	it('accepts the documented 422 shape', () => {
		expect(
			isFormValidationErrorResponse({
				error: 'form_validation_failed',
				field_errors: { steps: ['must be >= 1'] },
				coercions: [],
				stripped: [],
				message: 'Some fields need attention'
			})
		).toBe(true);
	});

	it('accepts field_errors as an empty object', () => {
		expect(isFormValidationErrorResponse({ error: 'form_validation_failed', field_errors: {} })).toBe(true);
	});

	it('accepts FastAPI detail-wrapped 422 bodies', () => {
		const wrapped = { detail: { error: 'form_validation_failed', field_errors: { steps: ['must be >= 1'] } } };
		expect(isFormValidationErrorResponse(wrapped)).toBe(false);
		expect(isFormValidationErrorResponse(normalizeGenerationErrorBody(wrapped))).toBe(true);
	});

	it('rejects a different error code', () => {
		expect(
			isFormValidationErrorResponse({ error: 'form_not_found', field_errors: { steps: ['x'] } })
		).toBe(false);
	});

	it('rejects a missing field_errors key', () => {
		expect(isFormValidationErrorResponse({ error: 'form_validation_failed' })).toBe(false);
	});

	it('rejects a malformed field_errors value (not string[])', () => {
		expect(
			isFormValidationErrorResponse({ error: 'form_validation_failed', field_errors: { steps: 'bad' } })
		).toBe(false);
	});

	it('rejects non-object input', () => {
		expect(isFormValidationErrorResponse(null)).toBe(false);
		expect(isFormValidationErrorResponse('nope')).toBe(false);
		expect(isFormValidationErrorResponse(undefined)).toBe(false);
	});
});

describe('classifyGenerationStartError', () => {
	it('classifies a 422 form_validation_failed body as field_validation', () => {
		const error = {
			response: {
				status: 422,
				data: {
					error: 'form_validation_failed',
					field_errors: { steps: ['must be >= 1'], cfg: ['must be > 0'] },
					message: 'Fix these fields'
				}
			}
		};
		const result = classifyGenerationStartError(error);
		expect(result.kind).toBe('field_validation');
		expect(result.fieldErrors).toEqual({ steps: ['must be >= 1'], cfg: ['must be > 0'] });
	});

	it('classifies a detail-wrapped 422 form_validation_failed body as field_validation', () => {
		const result = classifyGenerationStartError({
			response: {
				status: 422,
				data: { detail: { error: 'form_validation_failed', field_errors: { steps: ['must be >= 1'] } } }
			}
		});
		expect(result).toMatchObject({ kind: 'field_validation', fieldErrors: { steps: ['must be >= 1'] } });
	});

	it.each([
		['landscape', '960x544', '960x512'],
		['portrait', '544x960', '512x960']
	])('classifies exact nested %s geometry errors on both linked fields', (_orientation, original, suggested) => {
		const message = ltxGeometryMessage(original, suggested);
		const result = classifyGenerationStartError({
			response: { status: 400, data: { detail: { error: 'validation_error', message } } }
		});
		expect(result).toEqual({
			kind: 'field_validation',
			fieldErrors: { resolution: [message], upscale: [message] },
			message
		});
	});

	it('classifies an exact top-level geometry error as field validation', () => {
		const message = ltxGeometryMessage('960x544', '960x512');
		const result = classifyGenerationStartError({
			response: { status: 400, data: { error: 'validation_error', message } }
		});
		expect(result.kind).toBe('field_validation');
		expect(result.fieldErrors).toEqual({ resolution: [message], upscale: [message] });
	});

	it('keeps an exact geometry message at a non-400 status as other', () => {
		const message = ltxGeometryMessage('960x544', '960x512');
		const result = classifyGenerationStartError({
			response: { status: 422, data: { error: 'validation_error', message } }
		});
		expect(result).toEqual({ kind: 'other', fieldErrors: {}, message });
	});

	it('classifies a 404 form_not_found as other', () => {
		const error = {
			response: { status: 404, data: { error: 'form_not_found', message: 'No such form' } }
		};
		const result = classifyGenerationStartError(error);
		expect(result.kind).toBe('other');
		expect(result.fieldErrors).toEqual({});
		expect(result.message).toBe('No such form');
	});

	it('classifies a 500 as other', () => {
		const error = { response: { status: 500, data: { error: 'internal_error' } } };
		const result = classifyGenerationStartError(error);
		expect(result.kind).toBe('other');
		expect(result.message).toBe('internal_error');
	});

	it('keeps unrelated nested validation_error responses as toast failures with their exact message', () => {
		const result = classifyGenerationStartError({
			response: {
				status: 400,
				data: { detail: { error: 'validation_error', message: 'Resolution must be divisible by 32.' } }
			}
		});
		expect(result).toEqual({
			kind: 'other',
			fieldErrors: {},
			message: 'Resolution must be divisible by 32.'
		});
	});

	it('classifies a 422 with a malformed/absent field_errors key as other (defensive)', () => {
		const error = { response: { status: 422, data: { error: 'form_validation_failed' } } };
		const result = classifyGenerationStartError(error);
		expect(result.kind).toBe('other');
	});

	it('classifies a network error (no response) as other', () => {
		const error = new Error('Network Error');
		const result = classifyGenerationStartError(error);
		expect(result.kind).toBe('other');
		expect(result.message).toBe('Network Error');
	});

	it('falls back to a generic message when nothing else is available', () => {
		const result = classifyGenerationStartError({});
		expect(result.kind).toBe('other');
		expect(result.message).toBe('Failed to start generation.');
	});
});

describe('LTX geometry error parsing', () => {
	it('normalizes house top-level and FastAPI detail bodies', () => {
		const body = { error: 'validation_error', message: 'x' };
		expect(normalizeGenerationErrorBody(body)).toBe(body);
		expect(normalizeGenerationErrorBody({ detail: body })).toBe(body);
	});

	it('extracts the original and nearest resolutions plus the safe 2.0x fix', () => {
		const message = ltxGeometryMessage('960x544', '960x512');
		expect(parseLtxGeometryValidationError({ error: 'validation_error', message })).toEqual({
			originalResolution: '960x544',
			suggestedResolution: '960x512',
			safeUpscale: '2.0x',
			message
		});
		expect(parseLtxGeometryValidationMessage(message)).toEqual({
			originalResolution: '960x544',
			suggestedResolution: '960x512',
			safeUpscale: '2.0x'
		});
	});

	it('rejects malformed and near-match bodies', () => {
		const message = ltxGeometryMessage('960x544', '960x512');
		expect(parseLtxGeometryValidationError({ error: 'other', message })).toBeNull();
		expect(parseLtxGeometryValidationError({ error: 'validation_error', message: message.replace('1.5x', '1.6x') })).toBeNull();
		expect(parseLtxGeometryValidationError({ error: 'validation_error', message: message.replace('always achievable', 'usually achievable') })).toBeNull();
		expect(parseLtxGeometryValidationError({ error: 'validation_error', message: message.replace('45x25 grid', '0x25 grid') })).toBeNull();
		expect(parseLtxGeometryValidationError({ error: 'validation_error', message: 'Nearest achievable resolution: 960x512.' })).toBeNull();
	});
});

describe('deriveFieldErrorFix', () => {
	const message = ltxGeometryMessage('544x960', '512x960');

	it('offers the nearest achievable resolution as the resolution field value', () => {
		expect(deriveFieldErrorFix('resolution', [message])).toEqual({
			fieldName: 'resolution',
			value: '512x960',
			verb: 'Use',
			valueLabel: '512×960',
			resolvesFields: ['resolution', 'upscale']
		});
	});

	it('offers the always-achievable factor as the upscale field value', () => {
		expect(deriveFieldErrorFix('upscale', [message])).toEqual({
			fieldName: 'upscale',
			value: '2.0x',
			verb: 'Switch to',
			valueLabel: '2.0x',
			resolvesFields: ['resolution', 'upscale']
		});
	});

	it('labels the offer with the concrete value', () => {
		expect(formatFieldErrorFixLabel(deriveFieldErrorFix('resolution', [message])!)).toBe('Use 512×960');
		expect(formatFieldErrorFixLabel(deriveFieldErrorFix('upscale', [message])!)).toBe('Switch to 2.0x');
	});

	it('finds the suggestion alongside unrelated messages on the same field', () => {
		expect(deriveFieldErrorFix('resolution', ['must be set', message])).toMatchObject({ value: '512x960' });
	});

	it('offers nothing without an applicable suggestion', () => {
		expect(deriveFieldErrorFix('resolution', [])).toBeNull();
		expect(deriveFieldErrorFix('resolution', ['Resolution must be divisible by 32.'])).toBeNull();
		expect(deriveFieldErrorFix('resolution', [message.replace('1.5x', '1.6x')])).toBeNull();
		expect(deriveFieldErrorFix('steps', [message])).toBeNull();
	});
});

describe('clearFieldError', () => {
	it('removes the named field', () => {
		const input = { steps: ['bad'], cfg: ['bad'] };
		const result = clearFieldError(input, 'steps');
		expect(result).toEqual({ cfg: ['bad'] });
	});

	it('returns the same reference when the field has no error', () => {
		const input = { steps: ['bad'] };
		const result = clearFieldError(input, 'cfg');
		expect(result).toBe(input);
	});

	it('does not mutate the input', () => {
		const input = { steps: ['bad'] };
		clearFieldError(input, 'steps');
		expect(input).toEqual({ steps: ['bad'] });
	});
});

describe('countMatchingFieldErrors', () => {
	it('counts only fields present in both maps', () => {
		const fieldErrors = { steps: ['bad'], cfg: ['bad'], seed: [] };
		expect(countMatchingFieldErrors(fieldErrors, ['steps', 'cfg', 'sampler'])).toBe(2);
	});

	it('ignores fields with an empty message array', () => {
		expect(countMatchingFieldErrors({ steps: [] }, ['steps'])).toBe(0);
	});

	it('returns 0 for an empty field list', () => {
		expect(countMatchingFieldErrors({ steps: ['bad'] }, [])).toBe(0);
	});
});
