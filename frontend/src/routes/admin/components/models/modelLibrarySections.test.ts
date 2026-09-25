import { describe, it, expect } from 'vitest';
import {
	MODELS_ALL_SECTION,
	MODELS_ATTRIBUTES_SECTION,
	buildModelLibrarySections,
	modelLibrarySectionCounts
} from './modelLibrarySections';

describe('buildModelLibrarySections', () => {
	it('always starts with All models and ends with Attributes', () => {
		const sections = buildModelLibrarySections([{ type: 'lora', count: 3 }]);
		expect(sections[0]).toEqual({ id: MODELS_ALL_SECTION, label: 'All models', icon: 'cube' });
		expect(sections.at(-1)).toEqual({ id: MODELS_ATTRIBUTES_SECTION, label: 'Attributes', icon: 'sliders' });
		expect(sections).toHaveLength(3);
	});

	it('emits one section per model type, uppercased', () => {
		const sections = buildModelLibrarySections([
			{ type: 'checkpoint', count: 1 },
			{ type: 'lora', count: 2 }
		]);
		expect(sections.map((s) => s.id)).toEqual(['all', 'checkpoint', 'lora', 'attributes']);
		expect(sections.map((s) => s.label)).toEqual(['All models', 'CHECKPOINT', 'LORA', 'Attributes']);
	});

	it('is just All models + Attributes with no model types', () => {
		expect(buildModelLibrarySections([]).map((s) => s.id)).toEqual(['all', 'attributes']);
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
		expect(counts).toEqual({ all: 17, checkpoint: 5, lora: 12, attributes: 7 });
	});

	it('is zero for All models and Attributes with no model types', () => {
		expect(modelLibrarySectionCounts([], 0)).toEqual({ all: 0, attributes: 0 });
	});
});
