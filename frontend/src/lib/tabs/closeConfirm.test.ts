import { describe, expect, it } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import { describeTabClose } from './closeConfirm';

function tab(overrides: Partial<Tab> = {}, queue: Tab['generation']['queue'] = [], isGenerating = false): Tab {
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
		generation: { isGenerating, queue } as Tab['generation'],
		...overrides
	} as Tab;
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
