import { describe, it, expect } from 'vitest';
import { buildInspirationReuseSource, formatOmittedFieldsHint } from './reuseAdapter';
import { buildImportBundleTabData } from '$lib/utils/historyReuse';

describe('buildInspirationReuseSource', () => {
	it('carries the preset id, mode, and form data through', () => {
		const source = buildInspirationReuseSource({
			preset_id: 'sdxl/base',
			preset_name: 'SDXL Base',
			form_data: { seed: 42, steps: 30 },
			mode: 'img2img',
			omitted_fields: []
		});
		expect(source).toEqual({
			preset_id: 'sdxl/base',
			mode: 'img2img',
			form_name: null,
			form_data: { seed: 42, steps: 30 }
		});
	});

	it('falls back to an empty preset id, mode, and form data when absent', () => {
		const source = buildInspirationReuseSource({
			preset_id: null,
			preset_name: null,
			form_data: {},
			mode: null,
			omitted_fields: []
		});
		expect(source.preset_id).toBe('');
		expect(source.mode).toBe('');
		expect(source.form_data).toEqual({});
	});

	it('produces a source that historyReuse resolves to a usable tab (mode passes through)', () => {
		const source = buildInspirationReuseSource({
			preset_id: 'sdxl/base',
			preset_name: 'SDXL Base',
			form_data: { seed: 42 },
			mode: 'img2img',
			omitted_fields: []
		});
		const { tabData, backendUnavailable } = buildImportBundleTabData(source);
		expect(tabData.selectedPreset).toBe('sdxl/base');
		expect(tabData.selectedMode).toBe('img2img');
		expect(tabData.formData).toEqual({ seed: 42 });
		expect(backendUnavailable).toBe(false);
	});

	it('defaults the tab to txt2img when the snapshot has no mode', () => {
		const source = buildInspirationReuseSource({
			preset_id: 'sdxl/base',
			preset_name: 'SDXL Base',
			form_data: { seed: 42 },
			mode: null,
			omitted_fields: []
		});
		const { tabData } = buildImportBundleTabData(source);
		expect(tabData.selectedMode).toBe('txt2img');
	});
});

describe('formatOmittedFieldsHint', () => {
	it('returns an empty string when nothing was omitted', () => {
		expect(formatOmittedFieldsHint([])).toBe('');
	});

	it('phrases known media field names in plain language', () => {
		expect(formatOmittedFieldsHint(['init_image'])).toBe(
			'Not included: input image — provide your own.'
		);
	});

	it('humanizes unknown field names and joins multiple', () => {
		expect(formatOmittedFieldsHint(['init_image', 'lora_strength_notes'])).toBe(
			'Not included: input image, lora strength notes — provide your own.'
		);
	});
});
