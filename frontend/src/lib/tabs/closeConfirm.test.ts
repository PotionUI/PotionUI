import { describe, expect, it } from 'vitest';
import type { GenerationTab, ToolTab } from '$lib/types/tabs';
import { describeTabClose, tabHasUnsavedVariables } from './closeConfirm';

function tab(overrides: Partial<GenerationTab> = {}, queue: GenerationTab['generation']['queue'] = [], isGenerating = false): GenerationTab {
	return {
		id: 'tab-a',
		name: 'Fox study',
		selectedPreset: null,
		selectedMode: null,
		selectedSessionId: null,
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {},
		variables: {},
		generation: { isGenerating, queue } as GenerationTab['generation'],
		...overrides
	} as GenerationTab;
}

describe('describeTabClose', () => {
	it('names the tab and adds no warnings for a clean idle tab', () => {
		expect(describeTabClose(tab())).toEqual({
			title: 'Close tab?',
			message: '“Fox study” will be closed.'
		});
	});

	it('warns about a running generation', () => {
		expect(describeTabClose(tab({}, [], true)).message).toContain('generation running or queued');
	});

	it('warns about a queued-only generation', () => {
		const queued = [{ generation_id: 'g1', queue_position: 1, status: 'pending' as const }];
		expect(describeTabClose(tab({}, queued)).message).toContain('generation running or queued');
	});

	it('warns about typed prompt content but not a picked preset with default form data', () => {
		expect(describeTabClose(tab({ prompt: 'a fox' })).message).toContain('unsaved changes');
		expect(
			describeTabClose(tab({ selectedPreset: 'p', formData: { steps: 30 }, formPublished: true })).message
		).not.toContain('unsaved');
	});

	it('lists both warnings on separate lines', () => {
		expect(describeTabClose(tab({ prompt: 'a fox' }, [], true)).message.split('\n')).toHaveLength(3);
	});
});

describe('unsaved variables close warning', () => {
	const mood = { mood: { type: 'text' as const, value: 'noir' } };

	it('warns that the variables are not saved anywhere else on a tab that was never saved', () => {
		const message = describeTabClose(tab({ variables: mood })).message;
		expect(message).toContain("This tab's variables aren't saved anywhere else.");
		expect(tabHasUnsavedVariables(tab({ variables: mood }))).toBe(true);
	});

	it('stays quiet for a tab without variables', () => {
		expect(describeTabClose(tab({ prompt: 'a fox' })).message).not.toContain('variables');
		expect(tabHasUnsavedVariables(tab({ variables: {} }))).toBe(false);
	});

	it('stays quiet for a tab bound to a session', () => {
		expect(tabHasUnsavedVariables(tab({ variables: mood, selectedSessionId: 's1' }))).toBe(false);
		expect(describeTabClose(tab({ variables: mood, selectedSessionId: 's1' })).message).not.toContain('variables');
	});

	it('stays quiet for a tab applied from a saved prompt', () => {
		expect(tabHasUnsavedVariables(tab({ variables: mood, sourcePromptId: 'p1' }))).toBe(false);
	});
});

describe('closing a tool tab', () => {
	const toolTab = (dirty: number): ToolTab => ({
		kind: 'tool',
		id: 'tool-a',
		name: 'observatory-dusk',
		tool: 'image',
		source: null,
		state: null,
		dirty
	});

	it('asks to discard the unsaved edits, counting them', () => {
		expect(describeTabClose(toolTab(2))).toEqual({
			title: 'Discard 2 unsaved edits?',
			message: '“observatory-dusk” will be closed.',
			confirmLabel: 'Discard and close'
		});
		expect(describeTabClose(toolTab(1)).title).toBe('Discard 1 unsaved edit?');
	});

	it('uses the plain close prompt when nothing is unsaved', () => {
		expect(describeTabClose(toolTab(0))).toEqual({ title: 'Close tab?', message: '“observatory-dusk” will be closed.' });
	});
});
