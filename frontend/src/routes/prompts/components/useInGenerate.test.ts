import { describe, expect, it } from 'vitest';
import type { RichSegment } from '$lib/types/segments';
import type { GenerationTab } from '$lib/types/tabs';
import type { VariablesMap } from '$lib/utils/variableDefs';
import { buildUseInGenerateUpdate } from './useInGenerate';

function segment(content: string): RichSegment {
	return { type: 'content', content, chips: {}, enabled: true } as RichSegment;
}

const emptyTab = { promptSegments: [], negativePromptSegments: [], variables: {} };

describe('buildUseInGenerateUpdate', () => {
	it('refreshes the flattened prompt together with the segments', () => {
		const { updates } = buildUseInGenerateUpdate(emptyTab, [segment('a red fox')], null, '', 'p1');
		expect(updates.prompt).toBe('a red fox');
		expect(updates.promptSegments).toHaveLength(1);
		expect(updates.sourcePromptId).toBe('p1');
	});

	it('refreshes the negative text for a negative prompt', () => {
		const { updates } = buildUseInGenerateUpdate(emptyTab, [segment('blurry')], null, 'negative', null);
		expect(updates.negativePrompt).toBe('blurry');
		expect(updates.prompt).toBeUndefined();
	});

	it('reports one message that folds the imported variables in', () => {
		const variables = { mood: { type: 'text', values: ['calm'] } } as unknown as VariablesMap;
		const result = buildUseInGenerateUpdate(emptyTab, [segment('x')], variables, '', null);
		expect(result.importedVariables).toBe(1);
		expect(result.message).toBe('Prompt applied with 1 variable');
		expect(result.updates.variables).toBeDefined();
		expect(buildUseInGenerateUpdate(emptyTab, [segment('x')], null, '', null).message).toBe(
			'Prompt applied to the active Generate tab'
		);
	});

	it('replaces the old segments, keeps the other list and merges variables into a non-empty tab', () => {
		const tab = {
			promptSegments: [segment('old one'), segment('old two')] as unknown as GenerationTab['promptSegments'],
			negativePromptSegments: [segment('keep me')] as unknown as GenerationTab['negativePromptSegments'],
			variables: { colour: { type: 'text', values: ['red'] } } as unknown as VariablesMap
		};
		const incoming = { mood: { type: 'text', values: ['calm'] } } as unknown as VariablesMap;
		const { updates } = buildUseInGenerateUpdate(tab, [segment('fresh')], incoming, '', 'p2');
		expect(updates.promptSegments?.map((s) => s.content)).toEqual(['fresh']);
		expect(updates.prompt).toBe('fresh');
		expect(updates.negativePromptSegments).toBeUndefined();
		expect(Object.keys(updates.variables ?? {}).sort()).toEqual(['colour', 'mood']);
	});

	it('flattens with a space join regardless of the preset paragraph join (known gap)', () => {
		const { updates } = buildUseInGenerateUpdate(emptyTab, [segment('[Verse]'), segment('la la')], null, '', null);
		expect(updates.prompt).toBe('[Verse] la la');
	});
});
