import { describe, it, expect } from 'vitest';
import { promptCardTitle, summarizePromptVariables } from './promptCardDisplay';
import type { ChoiceVariableDef, VariablesMap } from '$lib/utils/variableDefs';

function choiceOf(options: ChoiceVariableDef['options']): ChoiceVariableDef {
	return { type: 'choice', options, mode: 'shuffle', pinnedIndex: null };
}

describe('promptCardTitle', () => {
	it('uses the name verbatim when the prompt has one', () => {
		expect(promptCardTitle({ name: 'Dancing girl factory', flattened_text: 'anything' })).toEqual({
			text: 'Dancing girl factory',
			untitled: false
		});
	});

	it('shows Untitled with no preview for an empty composition', () => {
		expect(promptCardTitle({ name: null, flattened_text: '' })).toEqual({ text: 'Untitled', untitled: true });
	});

	it('shows Untitled · "first words…" for a short unnamed prompt, without an ellipsis', () => {
		expect(promptCardTitle({ name: null, flattened_text: 'a matte black bottle' })).toEqual({
			text: 'Untitled · "a matte black bottle"',
			untitled: true
		});
	});

	it('truncates to the first 6 words with an ellipsis for a longer unnamed prompt', () => {
		const flattened = 'Live-action, vertical phone-video style, the woman from Picture 1 standing centered';
		expect(promptCardTitle({ name: null, flattened_text: flattened })).toEqual({
			text: 'Untitled · "Live-action, vertical phone-video style, the woman…"',
			untitled: true
		});
	});
});

describe('summarizePromptVariables', () => {
	it('is all-zero for no variables', () => {
		expect(summarizePromptVariables(null)).toEqual({ count: 0, linked: 0, broken: false });
		expect(summarizePromptVariables({})).toEqual({ count: 0, linked: 0, broken: false });
	});

	it('counts variables and options-with-a-condition across the whole map', () => {
		const variables: VariablesMap = {
			music: choiceOf(['hip hop', 'classical']),
			dance: choiceOf([
				{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } },
				{ text: 'waltz', when: { var: 'music', values: ['classical'] } },
				'freestyle'
			]),
			camera: { type: 'text', value: 'slow push in' }
		};

		expect(summarizePromptVariables(variables)).toEqual({ count: 3, linked: 2, broken: false });
	});

	it('flags broken when a condition references a missing variable', () => {
		const variables: VariablesMap = {
			dance: choiceOf([{ text: 'breaking', when: { var: 'ghost', values: ['hip hop'] } }])
		};

		expect(summarizePromptVariables(variables)).toEqual({ count: 1, linked: 1, broken: true });
	});
});
