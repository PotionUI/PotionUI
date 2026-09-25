import { describe, expect, it, vi } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import {
	anyTabHasUnsavedSessionChanges,
	handleBeforeUnload,
	tabHasUnsavedSessionChanges
} from './unsavedChangesGuard';

function tab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 'tab-a',
		name: 'Tab A',
		selectedPreset: 'preset-a',
		selectedMode: 'txt2img',
		selectedSessionId: null,
		savedSessionSignature: undefined,
		prompt: '',
		negativePrompt: '',
		formData: {},
		generation: {
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
		},
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		layoutMode: 'two',
		promptPanelWidth: 420,
		...overrides
	};
}

describe('tabHasUnsavedSessionChanges', () => {
	it('is false for a tab with no linked session', () => {
		expect(tabHasUnsavedSessionChanges(tab({ selectedSessionId: null }))).toBe(false);
	});

	it('is false before the saved baseline has hydrated (fresh reload, restore in flight)', () => {
		expect(
			tabHasUnsavedSessionChanges(
				tab({ selectedSessionId: 'session-a', savedSessionSignature: undefined })
			)
		).toBe(false);
	});

	it('is true when a historical version was restored without being saved yet', () => {
		expect(
			tabHasUnsavedSessionChanges(tab({ selectedSessionId: 'session-a', savedSessionSignature: null }))
		).toBe(true);
	});

	it('is false when the active mode matches the saved baseline', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt', formData: { steps: 20 } } });
		expect(
			tabHasUnsavedSessionChanges(
				tab({
					selectedSessionId: 'session-a',
					savedSessionSignature: saved,
					prompt: 'saved prompt',
					formData: { steps: 20 }
				})
			)
		).toBe(false);
	});

	it('is true when the active mode prompt diverges from the saved baseline', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt', formData: { steps: 20 } } });
		expect(
			tabHasUnsavedSessionChanges(
				tab({
					selectedSessionId: 'session-a',
					savedSessionSignature: saved,
					prompt: 'edited prompt',
					formData: { steps: 20 }
				})
			)
		).toBe(true);
	});

	it('is true when the active mode form data diverges from the saved baseline', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt', formData: { steps: 20 } } });
		expect(
			tabHasUnsavedSessionChanges(
				tab({
					selectedSessionId: 'session-a',
					savedSessionSignature: saved,
					prompt: 'saved prompt',
					formData: { steps: 99 }
				})
			)
		).toBe(true);
	});

	it('is true when a cached, non-active mode diverges from the saved baseline', () => {
		const saved = JSON.stringify({
			txt2img: { prompt: 'saved prompt', formData: {} },
			img2img: { prompt: 'saved other-mode prompt', formData: {} }
		});
		expect(
			tabHasUnsavedSessionChanges(
				tab({
					selectedSessionId: 'session-a',
					savedSessionSignature: saved,
					prompt: 'saved prompt',
					formData: {},
					modeStateByMode: {
						img2img: {
							prompt: 'edited other-mode prompt',
							negativePrompt: '',
							promptSegments: [],
							negativePromptSegments: [],
							formData: {}
						}
					}
				})
			)
		).toBe(true);
	});

	it('is false when a cached, non-active mode matches the saved baseline', () => {
		const saved = JSON.stringify({
			txt2img: { prompt: 'saved prompt', formData: {} },
			img2img: { prompt: 'saved other-mode prompt', formData: {} }
		});
		expect(
			tabHasUnsavedSessionChanges(
				tab({
					selectedSessionId: 'session-a',
					savedSessionSignature: saved,
					prompt: 'saved prompt',
					formData: {},
					modeStateByMode: {
						img2img: {
							prompt: 'saved other-mode prompt',
							negativePrompt: '',
							promptSegments: [],
							negativePromptSegments: [],
							formData: {}
						}
					}
				})
			)
		).toBe(false);
	});
});

describe('anyTabHasUnsavedSessionChanges', () => {
	it('is true when only one of several tabs is dirty', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt', formData: {} } });
		const clean = tab({ id: 'clean', selectedSessionId: 'session-a', savedSessionSignature: saved, prompt: 'saved prompt' });
		const dirty = tab({ id: 'dirty', selectedSessionId: 'session-b', savedSessionSignature: saved, prompt: 'edited prompt' });
		expect(anyTabHasUnsavedSessionChanges([clean, dirty])).toBe(true);
		expect(anyTabHasUnsavedSessionChanges([clean])).toBe(false);
	});
});

describe('handleBeforeUnload', () => {
	function fakeEvent() {
		return { preventDefault: vi.fn(), returnValue: '' };
	}

	it('does not prevent the unload when nothing is dirty', () => {
		const event = fakeEvent();
		handleBeforeUnload(event, [tab({ selectedSessionId: null })]);
		expect(event.preventDefault).not.toHaveBeenCalled();
		expect(event.returnValue).toBe('');
	});

	it('prevents the unload and sets returnValue when a tab has unsaved session changes', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt', formData: {} } });
		const event = fakeEvent();
		handleBeforeUnload(event, [
			tab({ selectedSessionId: 'session-a', savedSessionSignature: saved, prompt: 'edited prompt' })
		]);
		expect(event.preventDefault).toHaveBeenCalledTimes(1);
		expect(event.returnValue).toBe('');
	});
});
