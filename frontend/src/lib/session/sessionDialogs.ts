import { get, writable, type Writable } from 'svelte/store';
import {
	saveOrPrompt,
	type SessionController,
	type SessionControllerState
} from './sessionController';

export type SessionPanelState = Pick<
	SessionControllerState,
	| 'sessions'
	| 'selectedSessionId'
	| 'currentSession'
	| 'hasUnsavedChanges'
	| 'lastSavedTime'
	| 'isSessionLoading'
	| 'isQuickSaving'
	| 'historySessionId'
	| 'historyVersions'
	| 'isHistoryLoading'
	| 'historyError'
	| 'isRestoringVersion'
	| 'autoSaveEnabled'
	| 'autoSaveInterval'
	| 'sessionControlsEnabled'
>;

export type SessionPanelActions = Pick<
	SessionController,
	| 'select'
	| 'startNew'
	| 'togglePin'
	| 'toggleAutosave'
	| 'setAutosaveInterval'
	| 'openHistory'
	| 'closeHistory'
	| 'restoreVersion'
> & {
	save: () => void | Promise<void>;
	saveAs: () => void;
	rename: () => void;
	remove: () => void;
	reload: () => void;
	openScript?: (id: string) => void;
};

export interface SessionDialogsState {
	saveOpen: boolean;
	saveAsOpen: boolean;
	deleteOpen: boolean;
	reloadOpen: boolean;
	name: string;
	scriptSessionId: string | null;
}

export interface SessionDialogs {
	state: Writable<SessionDialogsState>;
	actions: SessionPanelActions;
	quickSave(): Promise<void>;
	openSave(): void;
	openSaveAs(): void;
	openDelete(): void;
	openReload(): void;
	confirmSave(isSaveAs: boolean): Promise<void>;
	confirmDelete(): Promise<void>;
	confirmReload(): Promise<void>;
	close(): void;
}

const CLOSED: SessionDialogsState = { saveOpen: false, saveAsOpen: false, deleteOpen: false, reloadOpen: false, name: '', scriptSessionId: null };

export function createSessionDialogs(controller: SessionController): SessionDialogs {
	const state = writable<SessionDialogsState>({ ...CLOSED });

	function close() {
		state.set({ ...CLOSED });
		controller.closeDialog('save');
		controller.closeDialog('delete');
		controller.closeDialog('reload');
		controller.clearFeedback();
	}

	function openSaveAs() {
		state.update((s) => ({ ...s, saveAsOpen: true, name: '' }));
		controller.openDialog('save');
		controller.clearNameError();
	}

	function openSave() {
		const current = get(controller.state).currentSession;
		if (current) {
			state.update((s) => ({ ...s, saveOpen: true, name: current.name }));
			controller.openDialog('save');
			controller.clearNameError();
		} else {
			openSaveAs();
		}
	}

	function openScript(id: string) {
		state.update((s) => ({ ...s, scriptSessionId: id }));
	}

	function openDelete() {
		state.update((s) => ({ ...s, deleteOpen: true }));
		controller.openDialog('delete');
	}

	function openReload() {
		const { currentSession, hasUnsavedChanges } = get(controller.state);
		if (!currentSession || !hasUnsavedChanges) return;
		state.update((s) => ({ ...s, reloadOpen: true }));
		controller.openDialog('reload');
	}

	async function confirmSave(isSaveAs: boolean) {
		if (await controller.saveAs(get(state).name, isSaveAs ? 'save-as' : 'rename')) close();
	}

	async function confirmDelete() {
		if (await controller.deleteSession()) {
			state.update((s) => ({ ...s, deleteOpen: false }));
			controller.closeDialog('delete');
		}
	}

	async function confirmReload() {
		await controller.reload();
		state.update((s) => ({ ...s, reloadOpen: false }));
		controller.closeDialog('reload');
	}

	function quickSave() {
		return saveOrPrompt(controller, openSaveAs);
	}

	return {
		state,
		actions: {
			select: controller.select,
			startNew: controller.startNew,
			togglePin: controller.togglePin,
			toggleAutosave: controller.toggleAutosave,
			setAutosaveInterval: controller.setAutosaveInterval,
			openHistory: controller.openHistory,
			closeHistory: controller.closeHistory,
			restoreVersion: controller.restoreVersion,
			save: quickSave,
			saveAs: openSaveAs,
			rename: openSave,
			remove: openDelete,
			reload: openReload,
			openScript
		},
		quickSave,
		openSave,
		openSaveAs,
		openDelete,
		openReload,
		confirmSave,
		confirmDelete,
		confirmReload,
		close
	};
}
