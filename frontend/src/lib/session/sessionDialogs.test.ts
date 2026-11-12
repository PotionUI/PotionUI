import { describe, it, expect, vi } from 'vitest';
import { get, writable } from 'svelte/store';
import { createSessionDialogs } from './sessionDialogs';
import type { SessionController } from './sessionController';

function fakeController(currentName: string | null, overrides: Partial<Record<string, unknown>> = {}) {
	const state = writable({ currentSession: currentName ? { id: 's1', name: currentName } : null, hasUnsavedChanges: false });
	const controller = {
		state,
		openDialog: vi.fn(),
		closeDialog: vi.fn(),
		clearNameError: vi.fn(),
		clearFeedback: vi.fn(),
		quickSave: vi.fn().mockResolvedValue(true),
		saveAs: vi.fn().mockResolvedValue(true),
		deleteSession: vi.fn().mockResolvedValue(true),
		select: vi.fn(),
		reload: vi.fn().mockResolvedValue(true),
		startNew: vi.fn(),
		togglePin: vi.fn(),
		toggleAutosave: vi.fn(),
		setAutosaveInterval: vi.fn(),
		openHistory: vi.fn(),
		closeHistory: vi.fn(),
		restoreVersion: vi.fn(),
		...overrides
	};
	return controller as unknown as SessionController & typeof controller;
}

describe('createSessionDialogs', () => {
	it('opens save-as with an empty name and declares the dialog', () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openSaveAs();
		expect(get(dialogs.state)).toMatchObject({ saveAsOpen: true, saveOpen: false, name: '' });
		expect(controller.openDialog).toHaveBeenCalledWith('save');
		expect(controller.clearNameError).toHaveBeenCalled();
	});

	it('opens rename prefilled with the current session name', () => {
		const dialogs = createSessionDialogs(fakeController('Alpha'));
		dialogs.openSave();
		expect(get(dialogs.state)).toMatchObject({ saveOpen: true, saveAsOpen: false, name: 'Alpha' });
	});

	it('falls back to save-as when there is no current session', () => {
		const dialogs = createSessionDialogs(fakeController(null));
		dialogs.openSave();
		expect(get(dialogs.state)).toMatchObject({ saveOpen: false, saveAsOpen: true });
	});

	it('opens the delete confirm and declares it', () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openDelete();
		expect(get(dialogs.state).deleteOpen).toBe(true);
		expect(controller.openDialog).toHaveBeenCalledWith('delete');
	});

	it('tells the controller about a cancel so a pending save cannot answer later', () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openSave();
		dialogs.close();
		expect(get(dialogs.state)).toEqual({ saveOpen: false, saveAsOpen: false, deleteOpen: false, reloadOpen: false, name: '', scriptSessionId: null });
		expect(controller.closeDialog).toHaveBeenCalledWith('save');
		expect(controller.closeDialog).toHaveBeenCalledWith('delete');
		expect(controller.clearFeedback).toHaveBeenCalled();
	});

	it('opens the script dialog for a session and closes it with the rest', () => {
		const dialogs = createSessionDialogs(fakeController('Alpha'));
		dialogs.actions.openScript?.('s9');
		expect(get(dialogs.state).scriptSessionId).toBe('s9');
		dialogs.close();
		expect(get(dialogs.state).scriptSessionId).toBeNull();
	});

	it('saves under the typed name and closes only on success', async () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openSaveAs();
		dialogs.state.update((s) => ({ ...s, name: 'Beta' }));
		controller.saveAs.mockResolvedValueOnce(false);
		await dialogs.confirmSave(true);
		expect(controller.saveAs).toHaveBeenCalledWith('Beta', 'save-as');
		expect(get(dialogs.state).saveAsOpen).toBe(true);
		await dialogs.confirmSave(true);
		expect(get(dialogs.state).saveAsOpen).toBe(false);
	});

	it('uses rename mode for the rename dialog', async () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openSave();
		await dialogs.confirmSave(false);
		expect(controller.saveAs).toHaveBeenCalledWith('Alpha', 'rename');
	});

	it('keeps the delete confirm open when the delete fails', async () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		dialogs.openDelete();
		controller.deleteSession.mockResolvedValueOnce(false);
		await dialogs.confirmDelete();
		expect(get(dialogs.state).deleteOpen).toBe(true);
		await dialogs.confirmDelete();
		expect(get(dialogs.state).deleteOpen).toBe(false);
		expect(controller.closeDialog).toHaveBeenCalledWith('delete');
	});

	it('quick save opens save-as when there is nothing to quick-save', async () => {
		const controller = fakeController(null, { quickSave: vi.fn().mockResolvedValue(false) });
		const dialogs = createSessionDialogs(controller);
		await dialogs.quickSave();
		expect(get(dialogs.state).saveAsOpen).toBe(true);
	});

	it('exposes controller actions plus the dialog-backed ones', () => {
		const controller = fakeController('Alpha');
		const dialogs = createSessionDialogs(controller);
		expect(dialogs.actions.select).toBe(controller.select);
		expect(dialogs.actions.saveAs).toBe(dialogs.openSaveAs);
		expect(dialogs.actions.rename).toBe(dialogs.openSave);
		expect(dialogs.actions.remove).toBe(dialogs.openDelete);
		expect(dialogs.actions.save).toBe(dialogs.quickSave);
		expect(dialogs.actions.reload).toBe(dialogs.openReload);
	});

	describe('reload from the last snapshot', () => {
		function dirtyDialogs(currentName: string | null = 'Alpha') {
			const controller = fakeController(currentName);
			controller.state.update((s) => ({ ...s, hasUnsavedChanges: true }));
			return { controller, dialogs: createSessionDialogs(controller) };
		}

		it('opens the confirm only while the current session has unsaved changes', () => {
			const { dialogs } = dirtyDialogs();
			dialogs.openReload();
			expect(get(dialogs.state).reloadOpen).toBe(true);
		});

		it('stays closed for a clean session and for a draft with no saved session', () => {
			const clean = createSessionDialogs(fakeController('Alpha'));
			clean.openReload();
			expect(get(clean.state).reloadOpen).toBe(false);
			const { dialogs: draft } = dirtyDialogs(null);
			draft.openReload();
			expect(get(draft.state).reloadOpen).toBe(false);
		});

		it('reloads the snapshot through the controller and closes the confirm', async () => {
			const { controller, dialogs } = dirtyDialogs();
			dialogs.openReload();
			await dialogs.confirmReload();
			expect(controller.reload).toHaveBeenCalledTimes(1);
			expect(controller.select).not.toHaveBeenCalled();
			expect(get(dialogs.state).reloadOpen).toBe(false);
		});

		it('keeps the confirm open while the reload is running and closes it once it is done', async () => {
			const { controller, dialogs } = dirtyDialogs();
			let finish!: (value: boolean) => void;
			controller.reload.mockReturnValueOnce(new Promise<boolean>((resolve) => (finish = resolve)));
			dialogs.openReload();
			const confirming = dialogs.confirmReload();
			expect(get(dialogs.state).reloadOpen).toBe(true);
			finish(true);
			await confirming;
			expect(get(dialogs.state).reloadOpen).toBe(false);
		});

		it('closes the confirm when the reload fails, leaving the report to the controller', async () => {
			const { controller, dialogs } = dirtyDialogs();
			controller.reload.mockResolvedValueOnce(false);
			dialogs.openReload();
			await dialogs.confirmReload();
			expect(get(dialogs.state).reloadOpen).toBe(false);
		});

		it('tells the controller while the confirm is open, so auto-save waits for the answer', () => {
			const { controller, dialogs } = dirtyDialogs();
			dialogs.openReload();
			expect(controller.openDialog).toHaveBeenCalledWith('reload');
			dialogs.close();
			expect(controller.closeDialog).toHaveBeenCalledWith('reload');
		});

		it('does not declare the confirm when it does not open', () => {
			const clean = fakeController('Alpha');
			createSessionDialogs(clean).openReload();
			const draft = fakeController(null);
			draft.state.update((s) => ({ ...s, hasUnsavedChanges: true }));
			createSessionDialogs(draft).openReload();
			expect(clean.openDialog).not.toHaveBeenCalledWith('reload');
			expect(draft.openDialog).not.toHaveBeenCalledWith('reload');
		});

		it('keeps the confirm declared while the reload runs and releases it afterwards', async () => {
			const { controller, dialogs } = dirtyDialogs();
			let finish!: (value: boolean) => void;
			controller.reload.mockReturnValueOnce(new Promise<boolean>((resolve) => (finish = resolve)));
			dialogs.openReload();
			const confirming = dialogs.confirmReload();
			expect(controller.closeDialog).not.toHaveBeenCalledWith('reload');
			finish(true);
			await confirming;
			expect(controller.closeDialog).toHaveBeenCalledWith('reload');
		});

		it('keeps the edits when the confirm is cancelled', () => {
			const { controller, dialogs } = dirtyDialogs();
			dialogs.openReload();
			dialogs.close();
			expect(get(dialogs.state).reloadOpen).toBe(false);
			expect(controller.reload).not.toHaveBeenCalled();
		});
	});
});
