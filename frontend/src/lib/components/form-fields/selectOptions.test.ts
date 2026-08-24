import { describe, expect, it } from 'vitest';
import { formatSelectOptions } from './selectOptions';

describe('formatSelectOptions', () => {
	it('carries sub_label through as description', () => {
		const options = [
			{ value: 'turbo', label: 'Turbo', sub_label: '8 steps, fastest' },
			{ value: 'quality', label: 'Quality', sub_label: '25 steps, DPM++ 2M' }
		];
		expect(formatSelectOptions(options, false)).toEqual([
			{ value: 'turbo', label: 'Turbo', description: '8 steps, fastest' },
			{ value: 'quality', label: 'Quality', description: '25 steps, DPM++ 2M' }
		]);
	});

	it('leaves description undefined for options without a sub_label', () => {
		const options = [{ value: 'balanced', label: 'Balanced' }];
		expect(formatSelectOptions(options, false)).toEqual([
			{ value: 'balanced', label: 'Balanced', description: undefined }
		]);
	});

	it('treats an empty-string sub_label as absent', () => {
		const options = [{ value: 'balanced', label: 'Balanced', sub_label: '' }];
		expect(formatSelectOptions(options, false)[0].description).toBeUndefined();
	});

	it('prepends a "-- None --" option with no description when allow_empty is set', () => {
		const options = [{ value: 'turbo', label: 'Turbo', sub_label: '8 steps, fastest' }];
		expect(formatSelectOptions(options, true)).toEqual([
			{ value: '', label: '-- None --', description: undefined },
			{ value: 'turbo', label: 'Turbo', description: '8 steps, fastest' }
		]);
	});

	it('mixes two-line and single-line options in the same select without inventing placeholders', () => {
		const options = [
			{ value: 'turbo', label: 'Turbo', sub_label: '8 steps, fastest' },
			{ value: 'balanced', label: 'Balanced' }
		];
		const formatted = formatSelectOptions(options, false);
		expect(formatted[0].description).toBe('8 steps, fastest');
		expect(formatted[1].description).toBeUndefined();
	});
});
