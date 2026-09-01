import { describe, expect, it } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import {
	decideNewWorkspaceAction,
	hasUnsavedWorkOutsideTab,
	tabHasUnsavedWork,
	workspaceHasUnsavedChanges
} from './newWorkspace';

function emptyGeneration(): Tab['generation'] {
	return {
		isGenerating: false,
		currentGeneration: null,
		currentProgress: null,
		pipeTimers: {},
		startedAt: null,
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null
	};
}

function pristineTab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 'tab-a',
		name: 'Generation 1',
		selectedPreset: null,
		selectedMode: null,
		selectedSessionId: null,
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {},
		variables: {},
		generation: emptyGeneration(),
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		layoutMode: 'two',
		promptPanelWidth: 420,
		...overrides
	};
}

describe('tabHasUnsavedWork', () => {
	it('is false for a freshly created, untouched tab', () => {
		expect(tabHasUnsavedWork(pristineTab())).toBe(false);
	});

	it('is true for a session-bound tab with the "historical restore is dirty" baseline (savedSessionSignature === null)', () => {
		expect(
			tabHasUnsavedWork(pristineTab({ selectedSessionId: 'session-a', savedSessionSignature: null }))
		).toBe(true);
	});

	it('is false for a session-bound tab with a real saved baseline or none loaded yet', () => {
		expect(
			tabHasUnsavedWork(
				pristineTab({ selectedSessionId: 'session-a', savedSessionSignature: '{"txt2img":{}}' })
			)
		).toBe(false);
		expect(tabHasUnsavedWork(pristineTab({ selectedSessionId: 'session-a' }))).toBe(false);
	});

	it('ignores draft-content fields on a tab that is bound to a session', () => {
		// The cheap cross-tab signal can't see a background tab's live edits
		// (only the active tab's mounted SessionCluster can) — content fields
		// only mean "unsaved" for a tab that was never saved as a session at all.
		expect(
			tabHasUnsavedWork(
				pristineTab({ selectedSessionId: 'session-a', prompt: 'a cat', selectedPreset: 'native/SDXL/realistic' })
			)
		).toBe(false);
	});

	it('is true for a never-saved tab with a preset picked', () => {
		expect(tabHasUnsavedWork(pristineTab({ selectedPreset: 'native/SDXL/realistic' }))).toBe(true);
	});

	it('is true for a never-saved tab with typed prompt content', () => {
		expect(tabHasUnsavedWork(pristineTab({ prompt: 'a cat' }))).toBe(true);
		expect(tabHasUnsavedWork(pristineTab({ negativePrompt: 'blurry' }))).toBe(true);
	});

	it('is true for a never-saved tab with prompt segments', () => {
		expect(
			tabHasUnsavedWork(pristineTab({ promptSegments: [{ id: 's1', content: 'a cat' } as any] }))
		).toBe(true);
	});

	it('is true for a never-saved tab with form data', () => {
		expect(tabHasUnsavedWork(pristineTab({ formData: { steps: 30 } }))).toBe(true);
	});

	it('is true for a never-saved tab with variables', () => {
		expect(tabHasUnsavedWork(pristineTab({ variables: { style: { type: 'text', value: 'x' } as any } }))).toBe(
			true
		);
	});

	it('ignores whitespace-only prompt text', () => {
		expect(tabHasUnsavedWork(pristineTab({ prompt: '   ' }))).toBe(false);
	});
});

describe('workspaceHasUnsavedChanges / decideNewWorkspaceAction', () => {
	it('wipes immediately when every tab is pristine', () => {
		const tabs = [pristineTab({ id: 't1' }), pristineTab({ id: 't2' })];
		expect(workspaceHasUnsavedChanges(tabs)).toBe(false);
		expect(decideNewWorkspaceAction(tabs)).toBe('wipe');
	});

	it('confirms when any single tab has unsaved work', () => {
		const tabs = [
			pristineTab({ id: 't1' }),
			pristineTab({ id: 't2', selectedPreset: 'native/SDXL/realistic' })
		];
		expect(workspaceHasUnsavedChanges(tabs)).toBe(true);
		expect(decideNewWorkspaceAction(tabs)).toBe('confirm');
	});
});

describe('hasUnsavedWorkOutsideTab', () => {
	it('is false when only the given tab is dirty', () => {
		const tabs = [
			pristineTab({ id: 'active', selectedSessionId: 's1', savedSessionSignature: null }),
			pristineTab({ id: 'other' })
		];
		expect(hasUnsavedWorkOutsideTab(tabs, 'active')).toBe(false);
	});

	it('is true when a different tab also has unsaved work', () => {
		const tabs = [
			pristineTab({ id: 'active', selectedSessionId: 's1', savedSessionSignature: null }),
			pristineTab({ id: 'other', selectedSessionId: 's2', savedSessionSignature: null })
		];
		expect(hasUnsavedWorkOutsideTab(tabs, 'active')).toBe(true);
	});
});
