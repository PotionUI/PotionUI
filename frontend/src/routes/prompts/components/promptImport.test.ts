import { describe, expect, it } from 'vitest';
import {
	buildPromptImportFormData,
	hasPromptImportInput,
	importFormatLabel,
	importSkipReasonCopy
} from './promptImport';

describe('importFormatLabel', () => {
	it('maps known format keys to their display label', () => {
		expect(importFormatLabel('styles_csv')).toBe('styles.csv');
		expect(importFormatLabel('style_json')).toBe('Style JSON');
		expect(importFormatLabel('wildcard_yaml')).toBe('Wildcard YAML');
		expect(importFormatLabel('lines')).toBe('One prompt per line');
		expect(importFormatLabel('image')).toBe('Image metadata');
	});

	it('falls back to the raw value for an unrecognized format', () => {
		expect(importFormatLabel('mystery_format')).toBe('mystery_format');
	});

	it('falls back to "Unknown" for a null-ish format', () => {
		expect(importFormatLabel(undefined as unknown as string)).toBe('Unknown');
	});
});

describe('importSkipReasonCopy', () => {
	it('maps no_metadata to friendly copy', () => {
		expect(importSkipReasonCopy('no_metadata')).toBe('No generation metadata found');
	});

	it('maps empty to friendly copy', () => {
		expect(importSkipReasonCopy('empty')).toBe('Empty');
	});

	it('shows an unrecognized reason as-is (the backend sends its raw message there)', () => {
		expect(importSkipReasonCopy('Some raw backend message')).toBe('Some raw backend message');
	});

	it('returns null when no reason is present', () => {
		expect(importSkipReasonCopy(undefined)).toBeNull();
	});
});

describe('hasPromptImportInput', () => {
	it('is false with no files and blank pasted text', () => {
		expect(hasPromptImportInput({ files: [], pastedText: '   ' })).toBe(false);
	});

	it('is true with at least one file', () => {
		const file = new File(['content'], 'styles.csv', { type: 'text/csv' });
		expect(hasPromptImportInput({ files: [file], pastedText: '' })).toBe(true);
	});

	it('is true with non-blank pasted text', () => {
		expect(hasPromptImportInput({ files: [], pastedText: 'a prompt' })).toBe(true);
	});
});

describe('buildPromptImportFormData', () => {
	it('appends every file under the files field', () => {
		const fileA = new File(['a'], 'a.csv', { type: 'text/csv' });
		const fileB = new File(['b'], 'b.png', { type: 'image/png' });
		const formData = buildPromptImportFormData({
			files: [fileA, fileB],
			pastedText: '',
			format: '',
			modelName: '',
			baseModel: ''
		});
		const files = formData.getAll('files');
		expect(files).toHaveLength(2);
		expect((files[0] as File).name).toBe('a.csv');
		expect((files[1] as File).name).toBe('b.png');
	});

	it('sends pasted text as an extra pasted.txt file part', () => {
		const formData = buildPromptImportFormData({
			files: [],
			pastedText: 'a masterpiece, best quality',
			format: '',
			modelName: '',
			baseModel: ''
		});
		const files = formData.getAll('files');
		expect(files).toHaveLength(1);
		const pastedFile = files[0] as File;
		expect(pastedFile.name).toBe('pasted.txt');
		expect(pastedFile.type).toBe('text/plain');
	});

	it('omits the pasted.txt part when the pasted text is only whitespace', () => {
		const formData = buildPromptImportFormData({
			files: [],
			pastedText: '   \n  ',
			format: '',
			modelName: '',
			baseModel: ''
		});
		expect(formData.getAll('files')).toHaveLength(0);
	});

	it('omits the format field when auto-detect is selected', () => {
		const formData = buildPromptImportFormData({
			files: [],
			pastedText: 'x',
			format: '',
			modelName: '',
			baseModel: ''
		});
		expect(formData.has('format')).toBe(false);
	});

	it('includes an explicit format', () => {
		const formData = buildPromptImportFormData({
			files: [],
			pastedText: 'x',
			format: 'lines',
			modelName: '',
			baseModel: ''
		});
		expect(formData.get('format')).toBe('lines');
	});

	it('trims and includes model_name / base_model only when non-blank', () => {
		const withValues = buildPromptImportFormData({
			files: [],
			pastedText: 'x',
			format: '',
			modelName: '  Illustrious  ',
			baseModel: ' SDXL '
		});
		expect(withValues.get('model_name')).toBe('Illustrious');
		expect(withValues.get('base_model')).toBe('SDXL');

		const withoutValues = buildPromptImportFormData({
			files: [],
			pastedText: 'x',
			format: '',
			modelName: '   ',
			baseModel: ''
		});
		expect(withoutValues.has('model_name')).toBe(false);
		expect(withoutValues.has('base_model')).toBe(false);
	});
});
