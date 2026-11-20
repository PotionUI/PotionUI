import { describe, expect, it } from 'vitest';
import { deriveMarkState } from '$lib/components/generation-panel/barState';
import { CANCEL_LABEL, START_LABEL, STARTING_LABEL, blockedReason, markOf } from './mark';

const ready = { hasSetup: true, promptRequired: true, prompt: 'a boat', regionRequired: false, hasArea: false, redoArea: false };

describe('markOf', () => {
	it('is ready with the start label when nothing blocks the edit', () => {
		expect(markOf({ running: false, starting: false, reason: null })).toEqual({ state: 'ready', label: START_LABEL, disabled: false });
	});

	it('is disabled with the plain reason as its label', () => {
		expect(markOf({ running: false, starting: false, reason: 'Describe the change first' })).toEqual({ state: 'disabled', label: 'Describe the change first', disabled: true });
	});

	it('turns into the cancel mark while a run is queued or running, even when something else would block', () => {
		expect(markOf({ running: true, starting: false, reason: 'Describe the change first' })).toEqual({ state: 'running', label: CANCEL_LABEL, disabled: false });
	});

	it('is disabled while the edit is being started', () => {
		expect(markOf({ running: false, starting: true, reason: null })).toEqual({ state: 'disabled', label: STARTING_LABEL, disabled: true });
	});

	it('uses the same states as the Generate page', () => {
		expect(markOf({ running: true, starting: false, reason: null }).state).toBe(deriveMarkState({ isGenerating: true, canGenerate: true, mode: 'once' }));
		expect(markOf({ running: false, starting: false, reason: 'x' }).state).toBe(deriveMarkState({ isGenerating: false, canGenerate: false, mode: 'once' }));
		expect(markOf({ running: false, starting: false, reason: null }).state).toBe(deriveMarkState({ isGenerating: false, canGenerate: true, mode: 'once' }));
	});
});

describe('blockedReason', () => {
	it('is null when everything is there', () => {
		expect(blockedReason(ready)).toBeNull();
	});

	it('names the first thing missing, in the order the member fixes it', () => {
		expect(blockedReason({ ...ready, hasSetup: false, prompt: '' })).toBe('Choose a preset first');
		expect(blockedReason({ ...ready, redoArea: true, prompt: '' })).toBe('Mark the area again');
		expect(blockedReason({ ...ready, regionRequired: true, prompt: '' })).toBe('Mark the object first');
		expect(blockedReason({ ...ready, prompt: '  ' })).toBe('Describe the change first');
	});

	it('needs no prompt or area when the task asks for none', () => {
		expect(blockedReason({ ...ready, promptRequired: false, prompt: '' })).toBeNull();
		expect(blockedReason({ ...ready, regionRequired: true, hasArea: true })).toBeNull();
	});
});
