import { get, writable } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerPorts } from './controller';
import { createEditorSessionController, sessionOf, snapshotOf } from './editorSessions';
import { createRunEngine } from './runEngine';

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({
	preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over
});
const task: EditorTaskInfo = { id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer()], default: null };
const schema = { properties: { main: { children: [{ name: 'steps', type: 'number', default: 28 }] } } };
const run: EditorRun = { id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [] };
const stored = (id: string, name: string, steps: number) => ({ id, name, created_at: '2026-11-10T10:00:00Z', updated_at: '2026-11-14T10:00:00Z', pinned: false, version: 3, data: { 'editor:edit': { formData: { steps } } } });

function build(extra: Partial<ControllerPorts> = {}) {
	let stack: EditStackState<unknown> = createStack();
	const ports = {
		tasks: vi.fn(async () => [task]),
		sessions: vi.fn(async () => [stored('s1', 'Soft portrait', 20), stored('s2', 'Crisp', 40)]),
		formulas: vi.fn(async () => []),
		schema: vi.fn(async () => schema),
		setDefault: vi.fn(async () => undefined),
		clearDefault: vi.fn(async () => undefined),
		saveSession: vi.fn(async () => ({ id: 'new', name: 'Mine', version: 1, updated_at: '2026-11-17T10:00:00Z' })),
		updateSession: vi.fn(async () => ({ id: 's1', name: 'Soft portrait', version: 4, updated_at: '2026-11-17T10:00:00Z' })),
		deleteSession: vi.fn(async () => undefined),
		pinSession: vi.fn(async () => undefined),
		versions: vi.fn(async () => [{ version_number: 2, created_at: 'x', summary: 'Edit' }]),
		version: vi.fn(async () => ({ version_number: 2, created_at: 'x', summary: 'Edit', data: { 'editor:edit': { formData: { steps: 9 } } } })),
		...extra
	};
	const engine = createRunEngine(
		{ start: async () => run, get: async () => run, cancel: async () => run, socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const controller = createGenerateController({
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed', revision: 1 }),
		landRegion: async () => ({ item_id: 'mask' }),
		areaSize: () => ({ width: 1, height: 1 }),
		stepsBeneath: async () => [],
		engine,
		ports
	});
	const toasts = { info: vi.fn(), warning: vi.fn(), error: vi.fn() };
	const sessions = createEditorSessionController(controller, () => 'edit_region', toasts);
	return { controller, sessions, ports, toasts, state: () => get(sessions.state) };
}

async function ready() {
	const t = build();
	await t.controller.prepare('edit_region');
	return t;
}

describe('the session object the control reads', () => {
	it('lists the editor sessions of the picked mode, with none selected on the model defaults', async () => {
		const t = await ready();
		const state = t.state();
		expect(state.sessionControlsEnabled).toBe(true);
		expect(state.sessions.map((entry) => entry.name)).toEqual(['Soft portrait', 'Crisp']);
		expect(state.currentSession).toBeNull();
		expect(state.selectedSessionId).toBe('');
		expect(state.autoSaveEnabled).toBe(false);
	});

	it('is disabled until a setup is picked', () => {
		const t = build();
		expect(t.state().sessionControlsEnabled).toBe(false);
		expect(t.state().sessions).toEqual([]);
	});

	it('turns a stored session into the shape the panel reads', () => {
		const made = sessionOf({ id: 's1', name: 'Soft', createdAt: null, updatedAt: '2026-11-14T10:00:00Z', values: { steps: 9 }, version: 3, pinned: true }, 'qwen', 'editor:edit');
		expect(made).toMatchObject({ id: 's1', preset_id: 'qwen', pinned: true, version: 3, updated_at: '2026-11-14T10:00:00Z', data: { 'editor:edit': { formData: { steps: 9 } } } });
	});

	it('is loading while the list of the picked mode has not arrived', async () => {
		const t = await ready();
		const ui = get(t.controller.ui);
		expect(snapshotOf({ ...ui, choices: {} }, 'edit_region', { isSaving: false, isQuickSaving: false, error: null, nameError: '', historySessionId: null, historyVersions: [], isHistoryLoading: false, historyError: null, isRestoringVersion: false, lastSavedTime: null }).isSessionLoading).toBe(true);
	});
});

describe('the actions the control calls', () => {
	it('loads a session, marks it dirty after an edit and goes back to the defaults with startNew', async () => {
		const t = await ready();
		await t.sessions.select('s1');
		expect(t.state().currentSession?.id).toBe('s1');
		expect(t.state().hasUnsavedChanges).toBe(false);
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 5 });
		expect(t.state().hasUnsavedChanges).toBe(true);
		t.sessions.startNew();
		await Promise.resolve();
		await Promise.resolve();
		expect(t.state().currentSession).toBeNull();
		expect(t.state().hasUnsavedChanges).toBe(false);
	});

	it('quick-saves the selected session, and hands the save-as dialog back when there is none', async () => {
		const t = await ready();
		expect(await t.sessions.quickSave()).toBe(false);
		await t.sessions.select('s1');
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 5 });
		expect(await t.sessions.quickSave()).toBe(true);
		expect(t.ports.updateSession).toHaveBeenCalledWith('s1', expect.objectContaining({ mode: 'editor:edit', base_version: 3, data: { 'editor:edit': { formData: { steps: 5 } } } }));
		expect(t.state().hasUnsavedChanges).toBe(false);
		expect(t.state().lastSavedTime).toBeInstanceOf(Date);
	});

	it('shows a failed quick save as the error of the control', async () => {
		const t = build({ updateSession: vi.fn(async () => Promise.reject({ response: { status: 409, data: { detail: { error: 'session_version_conflict' } } } })) });
		await t.controller.prepare('edit_region');
		await t.sessions.select('s1');
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 5 });
		await t.sessions.quickSave();
		expect(t.state().error).toContain('changed somewhere else');
	});

	it('saves as new and renames, putting a refusal on the name field', async () => {
		const t = await ready();
		expect(await t.sessions.saveAs('Mine', 'save-as')).toBe(true);
		expect(t.state().currentSession?.id).toBe('new');
		expect(await t.sessions.saveAs('crisp', 'rename')).toBe(false);
		expect(t.state().nameError).toBe('You already have a setup with that name. Choose another name.');
		t.sessions.clearNameError();
		expect(t.state().nameError).toBe('');
		expect(await t.sessions.saveAs('Mine too', 'rename')).toBe(true);
		expect(t.ports.updateSession).toHaveBeenCalledWith('new', expect.objectContaining({ name: 'Mine too' }));
	});

	it('deletes the selected session', async () => {
		const t = await ready();
		await t.sessions.select('s2');
		expect(await t.sessions.deleteSession()).toBe(true);
		expect(t.ports.deleteSession).toHaveBeenCalledWith('s2');
		expect(t.state().sessions.map((entry) => entry.id)).toEqual(['s1']);
	});

	it('pins a session', async () => {
		const t = await ready();
		await t.sessions.togglePin('s1');
		expect(t.state().sessions.find((entry) => entry.id === 's1')?.pinned).toBe(true);
	});

	it('toasts a failed pin and a failed delete the way the generation tab does', async () => {
		const t = build({ pinSession: vi.fn(async () => Promise.reject(new Error('offline'))), deleteSession: vi.fn(async () => Promise.reject(new Error('offline'))) });
		await t.controller.prepare('edit_region');
		await t.sessions.select('s1');
		await t.sessions.togglePin('s1');
		expect(t.toasts.error).toHaveBeenCalledWith('Failed to update pin');
		expect(await t.sessions.deleteSession()).toBe(false);
		expect(t.toasts.error).toHaveBeenCalledTimes(2);
	});

	it('lists the versions of a session and loads one as unsaved changes', async () => {
		const t = await ready();
		await t.sessions.openHistory('s1');
		expect(t.state()).toMatchObject({ historySessionId: 's1', isHistoryLoading: false });
		expect(t.state().historyVersions).toHaveLength(1);
		await t.sessions.restoreVersion('s1', 2);
		expect(t.state().currentSession?.id).toBe('s1');
		expect(t.state().hasUnsavedChanges).toBe(true);
		expect(t.state().isRestoringVersion).toBe(false);
		expect(t.toasts.info).toHaveBeenCalledWith(expect.stringContaining('Save to make it the latest.'));
		t.sessions.closeHistory();
		expect(t.state().historySessionId).toBeNull();
	});

	it('says when the history or a version cannot be loaded', async () => {
		const t = build({ versions: vi.fn(async () => Promise.reject(new Error('offline'))), version: vi.fn(async () => null) });
		await t.controller.prepare('edit_region');
		await t.sessions.openHistory('s1');
		expect(t.state().historyError).toBe('Could not load the history. Try again.');
		await t.sessions.restoreVersion('s1', 2);
		expect(t.toasts.error).toHaveBeenCalledWith('Could not load that save.');
	});

	it('reloads the last snapshot by dropping the unsaved changes', async () => {
		const t = await ready();
		await t.sessions.select('s1');
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 5 });
		expect(await t.sessions.reload()).toBe(true);
		expect(t.state().hasUnsavedChanges).toBe(false);
	});

	it('offers no auto-save and ignores the dialog hooks', async () => {
		const t = await ready();
		t.sessions.toggleAutosave();
		t.sessions.setAutosaveInterval(5000);
		t.sessions.openDialog('save');
		t.sessions.closeDialog('save');
		expect(t.state().autoSaveEnabled).toBe(false);
	});

	it('stops writing after destroy', async () => {
		const t = await ready();
		t.sessions.destroy();
		await t.sessions.saveAs('Late', 'save-as');
		expect(t.state().isSaving).toBe(false);
	});
});
