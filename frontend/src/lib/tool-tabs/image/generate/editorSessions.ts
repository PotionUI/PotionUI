import { derived, get, writable, type Readable } from 'svelte/store';
import type { Session, SessionVersionSummary } from '$lib/types/api';
import type { SessionController, SessionControllerState, SessionControllerToasts } from '$lib/session/sessionController';
import { toasts as defaultToasts } from '$lib/stores/toast';
import { timeAgo } from '$lib/utils/relativeTime';
import type { GenerateController, GenerateUi } from './controller';
import { offerKey, offerOf, type SessionChoice } from './setup';
import { isChanged } from './tweaks';

interface Local {
	isSaving: boolean;
	isQuickSaving: boolean;
	error: string | null;
	nameError: string;
	historySessionId: string | null;
	historyVersions: SessionVersionSummary[];
	isHistoryLoading: boolean;
	historyError: string | null;
	isRestoringVersion: boolean;
	lastSavedTime: Date | null;
}

const IDLE: Local = {
	isSaving: false,
	isQuickSaving: false,
	error: null,
	nameError: '',
	historySessionId: null,
	historyVersions: [],
	isHistoryLoading: false,
	historyError: null,
	isRestoringVersion: false,
	lastSavedTime: null
};

export function sessionOf(choice: SessionChoice, presetId: string, mode: string): Session {
	const stamp = choice.updatedAt ?? choice.createdAt ?? '';
	return {
		id: choice.id,
		preset_id: presetId,
		name: choice.name,
		data: { [mode]: { formData: choice.values } } as Session['data'],
		created_at: choice.createdAt ?? stamp,
		updated_at: stamp,
		pinned: choice.pinned === true,
		version: choice.version ?? 0
	};
}

export function snapshotOf(ui: GenerateUi, taskId: string | null, local: Local): SessionControllerState {
	const task = taskId ? ui.tasks.find((entry) => entry.id === taskId) : null;
	const choice = taskId ? ui.choice[taskId] : null;
	const offer = task && choice ? offerOf(task.offers, choice) : null;
	const choices = offer ? ui.choices[offerKey(offer)] : undefined;
	const sessions = offer && choices ? choices.sessions.map((entry) => sessionOf(entry, offer.preset_id, offer.mode)) : [];
	const selectedSessionId = choice?.setup.kind === 'session' ? choice.setup.id : '';
	const currentSession = sessions.find((entry) => entry.id === selectedSessionId) ?? null;
	return {
		sessions,
		selectedSessionId: currentSession ? selectedSessionId : '',
		currentSession,
		hasUnsavedChanges: !!taskId && isChanged(ui.tweaks[taskId]),
		lastSavedTime: local.lastSavedTime,
		isSessionLoading: !!offer && !choices,
		isSaving: local.isSaving,
		isQuickSaving: local.isQuickSaving,
		error: local.error,
		nameError: local.nameError,
		historySessionId: local.historySessionId,
		historyVersions: local.historyVersions,
		isHistoryLoading: local.isHistoryLoading,
		historyError: local.historyError,
		isRestoringVersion: local.isRestoringVersion,
		autoSaveEnabled: false,
		autoSaveInterval: 0,
		sessionControlsEnabled: !!offer
	};
}

export function createEditorSessionController(generate: GenerateController, taskId: () => string | null, toasts: SessionControllerToasts = defaultToasts): SessionController {
	const local = writable<Local>({ ...IDLE });
	const state: Readable<SessionControllerState> = derived([generate.ui, local], ([ui, own]) => snapshotOf(ui, taskId(), own));
	let destroyed = false;

	function patch(change: Partial<Local>): void {
		if (!destroyed) local.update((current) => ({ ...current, ...change }));
	}

	function id(): string | null {
		return taskId();
	}

	async function select(sessionId: string): Promise<void> {
		const task = id();
		const choice = task ? get(generate.ui).choice[task] : null;
		if (!task || !choice) return;
		patch({ error: null });
		await generate.pick(task, { preset_id: choice.preset_id, mode: choice.mode, setup: { kind: 'session', id: sessionId } });
	}

	function startNew(): void {
		const task = id();
		const choice = task ? get(generate.ui).choice[task] : null;
		if (!task || !choice) return;
		patch({ error: null });
		void generate.pick(task, { preset_id: choice.preset_id, mode: choice.mode, setup: { kind: 'defaults' } });
	}

	async function quickSave(): Promise<boolean> {
		const task = id();
		const choice = task ? get(generate.ui).choice[task] : null;
		if (!task || !choice || choice.setup.kind !== 'session') return false;
		patch({ isQuickSaving: true, error: null });
		const outcome = await generate.saveTo(task);
		patch({ isQuickSaving: false, error: outcome.ok ? null : outcome.message, lastSavedTime: outcome.ok ? new Date() : null });
		return true;
	}

	async function saveAs(name: string, mode: 'rename' | 'save-as'): Promise<boolean> {
		const task = id();
		if (!task) return false;
		patch({ isSaving: true, nameError: '', error: null });
		const outcome = mode === 'rename' ? await generate.saveTo(task, name) : await generate.saveAsNew(task, name);
		patch({ isSaving: false, nameError: outcome.ok ? '' : outcome.message, lastSavedTime: outcome.ok ? new Date() : null });
		return outcome.ok;
	}

	async function deleteSession(): Promise<boolean> {
		const task = id();
		if (!task) return false;
		patch({ error: null });
		const outcome = await generate.removeSession(task);
		if (!outcome.ok) toasts.error(outcome.message || 'Failed to delete session');
		return outcome.ok;
	}

	async function togglePin(sessionId: string): Promise<void> {
		const task = id();
		if (task && !(await generate.pinSession(task, sessionId))) toasts.error('Failed to update pin');
	}

	async function reload(): Promise<boolean> {
		const task = id();
		if (task) generate.reloadSnapshot(task);
		return true;
	}

	async function openHistory(sessionId: string): Promise<void> {
		patch({ historySessionId: sessionId, historyVersions: [], isHistoryLoading: true, historyError: null });
		try {
			const versions = await generate.listVersions(sessionId);
			if (get(local).historySessionId === sessionId) patch({ historyVersions: versions, isHistoryLoading: false });
		} catch {
			if (get(local).historySessionId === sessionId) patch({ isHistoryLoading: false, historyError: 'Could not load the history. Try again.' });
		}
	}

	function closeHistory(): void {
		patch({ historySessionId: null, historyVersions: [], isHistoryLoading: false, historyError: null });
	}

	async function restoreVersion(sessionId: string, versionNumber: number): Promise<void> {
		const task = id();
		if (!task) return;
		patch({ isRestoringVersion: true, error: null });
		const loaded = await generate.loadVersion(task, sessionId, versionNumber);
		patch({ isRestoringVersion: false });
		if (loaded) {
			const made = get(local).historyVersions.find((entry) => entry.version_number === versionNumber)?.created_at;
			toasts.info(`Loaded the save from ${made ? timeAgo(made) : 'earlier'} — Save to make it the latest.`);
		} else toasts.error('Could not load that save.');
	}

	return {
		state,
		setContext: () => undefined,
		start: () => undefined,
		destroy: () => {
			destroyed = true;
		},
		loadSessions: async () => {
			const task = id();
			const current = task ? generate.taskOf(task) : null;
			const choice = task ? get(generate.ui).choice[task] : null;
			const offer = current && choice ? offerOf(current.offers, choice) : null;
			if (offer) await generate.loadChoices(offer);
		},
		select,
		reload,
		quickSave,
		saveAs,
		deleteSession,
		togglePin,
		startNew,
		openDialog: () => undefined,
		closeDialog: () => undefined,
		openHistory,
		closeHistory,
		restoreVersion,
		startAutosave: () => undefined,
		stopAutosave: () => undefined,
		toggleAutosave: () => undefined,
		setAutosaveInterval: () => undefined,
		clearNameError: () => patch({ nameError: '' }),
		clearFeedback: () => patch({ nameError: '', error: null })
	};
}
