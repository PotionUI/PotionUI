import { describe, it, expect } from 'vitest';
import {
	MODELS_ALL_SECTION,
	MODELS_ATTRIBUTES_SECTION,
	MODEL_LIBRARY_SECTIONS,
	modelLibraryShellSection,
	modelLibrarySectionCounts,
	modelTypeRows
} from './modelLibrarySections';

describe('MODEL_LIBRARY_SECTIONS', () => {
	it('is All models then Attributes', () => {
		expect(MODEL_LIBRARY_SECTIONS.map((s) => s.id)).toEqual([MODELS_ALL_SECTION, MODELS_ATTRIBUTES_SECTION]);
		expect(MODEL_LIBRARY_SECTIONS.map((s) => s.label)).toEqual(['All models', 'Attributes']);
	});
});

describe('modelTypeRows', () => {
	it('emits one readable row per model type with its count', () => {
		const rows = modelTypeRows([
			{ type: 'text_encoder', count: 1 },
			{ type: 'lora', count: 2 }
		]);
		expect(rows.map((r) => r.type)).toEqual(['text_encoder', 'lora']);
		expect(rows.map((r) => r.count)).toEqual([1, 2]);
		expect(rows[0].label).not.toBe('TEXT_ENCODER');
		expect(rows[0].label).not.toContain('_');
	});

	it('is empty with no model types', () => {
		expect(modelTypeRows([])).toEqual([]);
	});
});

describe('modelLibraryShellSection', () => {
	it('keeps All models highlighted while a type narrows the list', () => {
		expect(modelLibraryShellSection('lora')).toBe(MODELS_ALL_SECTION);
		expect(modelLibraryShellSection(MODELS_ALL_SECTION)).toBe(MODELS_ALL_SECTION);
		expect(modelLibraryShellSection(MODELS_ATTRIBUTES_SECTION)).toBe(MODELS_ATTRIBUTES_SECTION);
	});
});

describe('modelLibrarySectionCounts', () => {
	it('sums every type into All models and carries the attributes count separately', () => {
		const counts = modelLibrarySectionCounts(
			[
				{ type: 'checkpoint', count: 5 },
				{ type: 'lora', count: 12 }
			],
			7
		);
		expect(counts).toEqual({ all: 17, attributes: 7 });
	});

	it('is zero for All models and Attributes with no model types', () => {
		expect(modelLibrarySectionCounts([], 0)).toEqual({ all: 0, attributes: 0 });
	});
});
