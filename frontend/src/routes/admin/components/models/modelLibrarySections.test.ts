import { describe, it, expect } from 'vitest';
import {
	MODELS_ALL_SECTION,
	MODELS_ATTRIBUTES_SECTION,
	MODELS_FOLDERS_SECTION,
	MODEL_LIBRARY_SECTIONS,
	modelLibraryShellSection,
	modelLibrarySectionCounts,
	modelTypeRows
} from './modelLibrarySections';

describe('MODEL_LIBRARY_SECTIONS', () => {
	it('is All models, then Attributes, then Folders', () => {
		expect(MODEL_LIBRARY_SECTIONS.map((s) => s.id)).toEqual([
			MODELS_ALL_SECTION,
			MODELS_ATTRIBUTES_SECTION,
			MODELS_FOLDERS_SECTION
		]);
		expect(MODEL_LIBRARY_SECTIONS.map((s) => s.label)).toEqual(['All models', 'Attributes', 'Folders']);
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

describe('modelTypeRows needs a type', () => {
	const types = [
		{ type: 'checkpoint', count: 5 },
		{ type: 'undefined', count: 3 },
		{ type: 'lora', count: 2 }
	];

	it('puts the row first with the friendly label and an attention flag', () => {
		const rows = modelTypeRows(types);
		expect(rows.map((r) => r.type)).toEqual(['undefined', 'checkpoint', 'lora']);
		expect(rows[0].label).toBe('Needs a type');
		expect(rows[0].attention).toBe(true);
		expect(rows[1].attention).toBe(false);
	});

	it('hides the row when nothing needs a type', () => {
		const rows = modelTypeRows([{ type: 'undefined', count: 0 }, { type: 'lora', count: 2 }]);
		expect(rows.map((r) => r.type)).toEqual(['lora']);
	});

	it('keeps an empty row while it is the selected filter', () => {
		const rows = modelTypeRows([{ type: 'undefined', count: 0 }, { type: 'lora', count: 2 }], 'undefined');
		expect(rows.map((r) => r.type)).toEqual(['undefined', 'lora']);
	});

	it('counts the row in All models', () => {
		expect(modelLibrarySectionCounts(types, 0).all).toBe(10);
	});
});

describe('modelLibraryShellSection', () => {
	it('keeps All models highlighted while a type narrows the list', () => {
		expect(modelLibraryShellSection('lora')).toBe(MODELS_ALL_SECTION);
		expect(modelLibraryShellSection(MODELS_ALL_SECTION)).toBe(MODELS_ALL_SECTION);
		expect(modelLibraryShellSection(MODELS_ATTRIBUTES_SECTION)).toBe(MODELS_ATTRIBUTES_SECTION);
		expect(modelLibraryShellSection(MODELS_FOLDERS_SECTION)).toBe(MODELS_FOLDERS_SECTION);
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
