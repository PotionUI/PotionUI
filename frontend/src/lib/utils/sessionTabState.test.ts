import { describe, expect, it } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import {
	collectTabSessionData,
	isSessionGoneError,
	isSessionMissingResponse,
	normalizeSessionBaselineFormData,
	sessionIsDirty,
	shouldHydrateSessionSelection
} from './sessionTabState';
import { toPersistedTab } from '$lib/stores/tabPersistence';

function tab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 'tab-a',
		name: 'Tab A',
		selectedPreset: 'preset-a',
		selectedMode: 'txt2img',
		selectedSessionId: 'session-a',
		prompt: 'edited prompt',
		negativePrompt: 'edited negative',
		formData: { steps: 31 },
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
		leftPanelCollapsed: true,
		layoutMode: 'three',
		promptPanelWidth: 420,
		...overrides
	};
}

describe('session tab remount state', () => {
	it('keeps an existing selected id metadata-only on a component remount', () => {
		expect(shouldHydrateSessionSelection(false, '', 'session-a')).toBe(false);
		expect(shouldHydrateSessionSelection(true, 'session-a', 'session-a')).toBe(false);
		expect(shouldHydrateSessionSelection(true, 'session-a', 'session-b')).toBe(true);
	});

	it('preserves the dirty indication against the saved baseline across a remount', () => {
		const saved = JSON.stringify({ txt2img: { prompt: 'saved prompt' } });
		const draft = JSON.stringify({ txt2img: { prompt: 'edited prompt' } });
		expect(sessionIsDirty(true, saved, draft)).toBe(true);
		expect(sessionIsDirty(true, saved, saved)).toBe(false);
		// A history restore deliberately has no latest-server baseline yet.
		expect(sessionIsDirty(true, null, draft)).toBe(true);
	});

	it('captures form, prompt, and layout together when server hydration records its baseline', () => {
		const snapshot = collectTabSessionData(tab(), 'txt2img', {
			txt2img: { prompt: 'saved prompt', formData: { steps: 20 } }
		});

		expect(snapshot.txt2img).toMatchObject({
			prompt: 'edited prompt',
			negativePrompt: 'edited negative',
			formData: { steps: 31 },
			layoutMode: 'three',
			leftPanelCollapsed: true
		});
	});

	it('normalizes one missing schema default without rebaselining the next edit or a remount', () => {
		const serverTab = tab({
			prompt: 'saved prompt',
			negativePrompt: 'saved negative',
			formData: { steps: 20 },
			layoutMode: 'two',
			leftPanelCollapsed: false
		});
		const rawBaseline = JSON.stringify(collectTabSessionData(serverTab, 'txt2img'));
		const normalizedFormData = { steps: 20, guidance: 7 };
		const normalizedBaseline = normalizeSessionBaselineFormData(
			rawBaseline,
			'txt2img',
			normalizedFormData
		)!;
		const normalizedTab = tab({
			prompt: 'saved prompt',
			negativePrompt: 'saved negative',
			formData: normalizedFormData,
			layoutMode: 'two',
			leftPanelCollapsed: false
		});
		expect(sessionIsDirty(true, normalizedBaseline, JSON.stringify(collectTabSessionData(normalizedTab, 'txt2img')))).toBe(false);

		const draft = tab({
			prompt: 'edited prompt',
			negativePrompt: 'saved negative',
			formData: { steps: 20, guidance: 8 },
			layoutMode: 'three',
			leftPanelCollapsed: true
		});
		const beforeRemount = structuredClone(draft);
		const draftSignature = JSON.stringify(collectTabSessionData(draft, 'txt2img'));

		expect(sessionIsDirty(true, normalizedBaseline, draftSignature)).toBe(true);
		expect(shouldHydrateSessionSelection(true, 'session-a', 'session-a')).toBe(false);
		expect(draft).toEqual(beforeRemount);
		expect(sessionIsDirty(true, normalizedBaseline, draftSignature)).toBe(true);
	});

	it('does not persist transient server-baseline state across a full reload', () => {
		const persisted = toPersistedTab(tab({
			savedSessionSignature: 'server-only',
			sessionBaselineAwaitingFormNormalization: true
		}));
		expect(persisted).not.toHaveProperty('savedSessionSignature');
		expect(persisted).not.toHaveProperty('sessionBaselineAwaitingFormNormalization');
	});

	it('consumes an empty hydrated form before its first user edit', () => {
		const emptyServerTab = tab({
			prompt: 'saved prompt',
			formData: {},
			layoutMode: 'two',
			leftPanelCollapsed: false
		});
		const rawBaseline = JSON.stringify(collectTabSessionData(emptyServerTab, 'txt2img'));
		const consumedBaseline = normalizeSessionBaselineFormData(rawBaseline, 'txt2img', {})!;
		const normalizedEmpty = tab({
			prompt: 'saved prompt',
			formData: {},
			layoutMode: 'two',
			leftPanelCollapsed: false
		});
		expect(sessionIsDirty(true, consumedBaseline, JSON.stringify(collectTabSessionData(normalizedEmpty, 'txt2img')))).toBe(false);

		const firstEdit = tab({
			prompt: 'saved prompt',
			formData: { steps: 1 },
			layoutMode: 'two',
			leftPanelCollapsed: false
		});
		expect(sessionIsDirty(true, consumedBaseline, JSON.stringify(collectTabSessionData(firstEdit, 'txt2img')))).toBe(true);
	});
});

describe('distinguishing a missing session from an unreachable backend', () => {
	it('treats a definitive not-found/access-denied response as missing', () => {
		expect(isSessionMissingResponse({ success: false, error: 'session_not_found' })).toBe(true);
		expect(isSessionMissingResponse({ success: false, error: 'session_access_denied' })).toBe(true);
	});

	it('does not treat a success response or an unrelated error code as missing', () => {
		expect(isSessionMissingResponse({ success: true })).toBe(false);
		expect(isSessionMissingResponse({ success: false, error: 'internal_error' })).toBe(false);
		expect(isSessionMissingResponse(null)).toBe(false);
		expect(isSessionMissingResponse(undefined)).toBe(false);
	});

	it('treats only a thrown HTTP 404 as proof the session is gone', () => {
		expect(isSessionGoneError({ response: { status: 404 } })).toBe(true);
	});

	it('does not treat a 5xx, a plain error, or no error as proof the session is gone', () => {
		expect(isSessionGoneError({ response: { status: 500 } })).toBe(false);
		expect(isSessionGoneError(new Error('Network Error'))).toBe(false);
		expect(isSessionGoneError(undefined)).toBe(false);
	});
});
