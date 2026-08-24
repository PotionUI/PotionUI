import { describe, expect, it, vi } from 'vitest';
import { applyFieldErrorFix, type FormFieldErrorActions } from './fieldErrorActionsContext';
import { deriveFieldErrorFix } from '$lib/utils/formValidationErrors';

function fakeActions(): FormFieldErrorActions & {
	setFieldValue: ReturnType<typeof vi.fn>;
	clearFields: ReturnType<typeof vi.fn>;
} {
	return { setFieldValue: vi.fn(), clearFields: vi.fn() };
}

const geometryMessage =
	'LTX 1.5x upscale of 544x960 is not achievable: the upsampled stage-1 latent lands on a 25x45 grid, ' +
	'but the refine stage is configured for 26x45 (target resolution 816x1440) -- 1.5x only lands on a clean ' +
	'grid when both the width and height are multiples of 64px. Nearest achievable resolution: 512x960. ' +
	'You can also switch Upscale to 2.0x, which is always achievable at any resolution.';

describe('applyFieldErrorFix', () => {
	it('writes the suggested resolution into the resolution field', () => {
		const actions = fakeActions();
		applyFieldErrorFix(deriveFieldErrorFix('resolution', [geometryMessage])!, actions);
		expect(actions.setFieldValue).toHaveBeenCalledWith('resolution', '512x960');
	});

	it('writes the safe factor into the upscale field', () => {
		const actions = fakeActions();
		applyFieldErrorFix(deriveFieldErrorFix('upscale', [geometryMessage])!, actions);
		expect(actions.setFieldValue).toHaveBeenCalledWith('upscale', '2.0x');
	});

	it('clears the errors on every field the one write resolves', () => {
		const actions = fakeActions();
		applyFieldErrorFix(deriveFieldErrorFix('resolution', [geometryMessage])!, actions);
		expect(actions.clearFields).toHaveBeenCalledWith(['resolution', 'upscale']);
	});

	it('writes the value before clearing, so the cleared field keeps its new value', () => {
		const order: string[] = [];
		const actions: FormFieldErrorActions = {
			setFieldValue: () => order.push('set'),
			clearFields: () => order.push('clear')
		};
		applyFieldErrorFix(deriveFieldErrorFix('upscale', [geometryMessage])!, actions);
		expect(order).toEqual(['set', 'clear']);
	});
});
