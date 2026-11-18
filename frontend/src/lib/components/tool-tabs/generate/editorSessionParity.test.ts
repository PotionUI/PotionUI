import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { writable } from 'svelte/store';
import { render } from 'svelte/server';
import SessionControl from '$lib/components/session/SessionControl.svelte';
import SessionHost from '$lib/components/session/SessionHost.svelte';
import { createSessionDialogs, type SessionPanelState } from '$lib/session/sessionDialogs';
import type { SessionController } from '$lib/session/sessionController';

const stored = { id: 's1', name: 'Soft portrait', pinned: false, updated_at: null, version: 1, data: {} };
const state = {
	sessions: [stored],
	currentSession: stored,
	selectedSessionId: 's1',
	hasUnsavedChanges: true,
	isSessionLoading: false,
	isSaving: false,
	isQuickSaving: false,
	error: null,
	nameError: '',
	sessionControlsEnabled: true,
	autoSaveEnabled: false,
	autoSaveInterval: 0
} as unknown as SessionPanelState;

const noop = () => undefined;
const actions = { select: noop, save: noop, saveAs: noop, remove: noop, reload: noop, startNew: noop, togglePin: noop, toggleAutosave: noop, setAutosaveInterval: noop, openHistory: noop, closeHistory: noop, restoreVersion: noop } as never;

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('the editor dock session controls are the generation tab ones', () => {
	const pickers = read('./EditorPickers.svelte');
	const toolbar = read('../../../../routes/generate/components/ContextToolbar.svelte');

	it('mounts SessionControl and SessionHost, with no dialog or label of its own', () => {
		expect(pickers).toContain("from '$lib/components/session/SessionControl.svelte'");
		expect(pickers).toContain("from '$lib/components/session/SessionHost.svelte'");
		expect(toolbar).toContain("from '$lib/components/session/SessionControl.svelte'");
		for (const own of ['SessionSaveModal', 'ConfirmModal', 'Delete Session', 'Reload last snapshot', 'Save as a new session']) {
			expect(pickers).not.toContain(own);
		}
	});

	it('renders the same session trigger, save and reload controls as the generation toolbar', () => {
		const dock = render(SessionControl, { props: { session: state, actions, compact: true, autosave: false, shortcut: false } }).body;
		const generation = render(SessionControl, { props: { session: state, actions, compact: true } }).body;
		for (const marker of ['aria-label="Session"', 'aria-label="Save session"', 'aria-label="Reload from last snapshot"', 'data-testid="session-save"', '>Save<']) {
			expect(dock).toContain(marker);
			expect(generation).toContain(marker);
		}
	});

	it('renders the delete confirmation from the shared host', () => {
		const controller = { state: writable(state), openDialog: vi.fn(), closeDialog: vi.fn(), clearNameError: vi.fn(), clearFeedback: vi.fn() } as unknown as SessionController;
		const dialogs = createSessionDialogs(controller);
		dialogs.openDelete();
		const out = render(SessionHost, { props: { controller, dialogs, scripts: false, shortcut: false } }).body;
		expect(out).toContain('Delete Session');
		expect(out).toContain('Soft portrait');
	});
});
