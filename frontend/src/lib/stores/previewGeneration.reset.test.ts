import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listPresets: vi.fn(),
		getSessionsForPreset: vi.fn(),
		getPresetModes: vi.fn(),
		getToken: vi.fn(() => null),
		// previewGeneration.ts imports createGenerationSocket() from
		// $lib/services/websocket, which imports the authStore singleton -
		// its module-level `api.setOnAuthExpired(...)` registration runs on
		// import even though this file never triggers a login.
		setOnAuthExpired: vi.fn()
	}
}));

import { previewGenerationStore } from './previewGeneration';
import { createBlankEditorSegment, flattenRichSegments } from '$lib/utils/richSegments';

describe('previewGenerationStore.reset()', () => {
	beforeEach(() => {
		previewGenerationStore.reset();
	});

	it('clears preset/session/prompt config a different user must not inherit', () => {
		previewGenerationStore.setSelectedPresetId('preset-a');
		previewGenerationStore.setSelectedSessionId('session-a');
		previewGenerationStore.setPromptSegments([
			{ ...createBlankEditorSegment(), content: 'a very specific photo of << value >>' }
		]);
		previewGenerationStore.setNegativePrompt('blurry');
		expect(get(previewGenerationStore).selectedPresetId).toBe('preset-a');

		previewGenerationStore.reset();

		const state = get(previewGenerationStore);
		expect(state.selectedPresetId).toBe('');
		expect(state.selectedSessionId).toBe('');
		expect(flattenRichSegments(state.promptSegments)).toBe('A photo of << value >>');
		expect(state.negativePrompt).toBe('');
		expect(state.presets).toEqual([]);
		expect(state.sessions).toEqual([]);
	});

	it('is safe to call when no socket was ever connected', () => {
		expect(() => previewGenerationStore.reset()).not.toThrow();
	});
});
