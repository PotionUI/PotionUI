// The session workflow — list load (with outage retry), selection/hydration,
// autosave, quick save, save-as/rename, version restore, delete — as one
// explicit controller instead of a copy in each mounted view. Two views own
// the same workflow (the console bar's SessionCluster and the tabs-row
// SessionPill) and drifted apart in the past; they now differ only in chrome.
//
// It is deliberately NOT a module singleton: one instance per mounted view,
// created with the collaborators it needs so a test can drive it with fake
// timers and deferred promises.
//
// A session command (autosave, save, save-as, restore, delete) resolves long
// after the user may have picked another session or switched tab, and its
// completion writes tab-scoped state (`recordSavedBaseline` -> tabs store).
// The session list entry for the saved id is always safe to refresh (an id
// absent from the current list is simply not matched); the ACTIVE state —
// currentSession, the saved baseline, the dirty/saving flags, the error — must
// only be written by a command that still owns the selection AND is the newest
// word on it. Three counters decide that, all captured into the command's token
// when it starts:
//
//   - `generation` — moves on every selection/tab/preset/mode change and on
//     destroy, so a command that outlived its selection writes nothing.
//   - `seq` + the newest issued seq per intent — two saves of the SAME session
//     share a generation, so ordering between them needs its own counter: an
//     older save is superseded the moment a newer one is issued, whichever
//     resolves first, and its success, its failure and its stale snapshot are
//     all dropped.
//   - `lastAppliedSeq` — the seq of the last command that actually wrote the
//     active state, so a command issued before a load/restore/delete landed
//     cannot overwrite it afterwards, across intents.
//
// The sessions LIST and the view's modal are scoped to the context the command
// started under (`listGeneration`: tab + preset + mode, unaffected by mere
// selection changes) rather than to the selection: a save-as that completes
// after a preset switch must not insert its record into the new preset's list,
// nor answer for a dialog the user has since opened under it.
//
// A command that also drives a DIALOG or a busy control (save-as, delete,
// restore) needs one more thing on top of that: the view has to be told
// whether to close, and its busy flag has to come down exactly once. Each such
// kind keeps an in-flight handle naming the operation the view is waiting on,
// so an older completion answers for no dialog it no longer owns
// (`ownsDialog`) while still putting down the flag it raised itself.
//
// READS (hydrating a session, the preset's session list, a session's version
// list) need the same treatment for the opposite reason: they do not write the
// server, but they DO replace the selection, the rows, the error and the
// loading flags, and the list read reschedules itself after an outage. Each
// read captures a `SessionReadToken` and may publish only while it is the
// newest request of its kind and the context it was issued under is still on
// screen; see `sessionReadOwnsState` for which counters each kind is scoped to.

import { writable, type Readable } from 'svelte/store';
import type {
	ModeBasedSessionData,
	PresetModeVariant,
	Session,
	SessionData,
	SessionVersionDetail,
	SessionVersionSummary
} from '$lib/types/api';
import type { Tab } from '$lib/types/tabs';
import type { GenerationLayoutMode } from '$lib/stores/generationLayout';
import { resolveVariant } from '$lib/utils/variants';
import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
import { seedModeStateFromSessionData } from '$lib/utils/modeState';
import { timeAgo } from '$lib/utils/relativeTime';
import {
	collectTabSessionData,
	isSessionGoneError,
	isSessionMissingResponse,
	sessionIsDirty,
	shouldHydrateSessionSelection
} from '$lib/utils/sessionTabState';

/** Commands that write the active state, ordered against their own kind. */
export type SessionCommandIntent =
	| 'quick-save'
	| 'autosave'
	| 'save-as'
	| 'restore'
	| 'delete';

/** Captured when an async session command starts. */
export interface SessionCommandToken {
	/** The session selected at the start of the command; '' when none was. */
	sessionId: string;
	/** The selection generation the command started under. */
	generation: number;
	/** Monotonic across every command this controller issues. */
	seq: number;
	/** The list context (tab + preset + mode) the command started under. */
	listGeneration: number;
	intent: SessionCommandIntent;
}

/** The controller's live counters at the moment a command completes. */
export interface SessionCommandOwnership {
	generation: number;
	activeSessionId: string;
	/** Newest seq issued for the completing command's intent. */
	newestIssuedSeq: number;
	/** Seq of the last command that wrote the active state. */
	lastAppliedSeq: number;
}

export function sessionCommandOwnsActiveState(
	token: SessionCommandToken,
	current: SessionCommandOwnership
): boolean {
	if (token.generation !== current.generation) return false;
	if (token.sessionId !== current.activeSessionId) return false;
	// A newer command of the same intent supersedes this one the moment it is
	// issued, not when it completes: whichever lands first, the older one's
	// snapshot is stale and its outcome (success or failure) is not the answer.
	if (token.seq !== current.newestIssuedSeq) return false;
	return token.seq > current.lastAppliedSeq;
}

/** Asynchronous reads that publish into the controller's state, each ordered
 *  against its own kind. */
export type SessionReadKind = 'session' | 'list' | 'history';

/** Captured when an async session read starts. */
export interface SessionReadToken {
	kind: SessionReadKind;
	/** Monotonic per kind: only the newest request of a kind may publish. */
	seq: number;
	/** The tab whose store this read's writes belong to. */
	tabId: string;
	presetId: string | null;
	mode: string | null;
	/**
	 * The selection generation, for a read that owns the selection (`select`
	 * and the programmatic sync — both replace `currentSession` and write the
	 * tab). Null for the list and the version list, which belong to the preset
	 * rather than to whichever session is picked inside it: a mere selection
	 * change, or a tab switch that lands on the same preset, leaves their
	 * result perfectly valid and re-issues nothing.
	 */
	generation: number | null;
	/** The session the read is about; '' for the preset's session list. */
	targetId: string;
}

/** The controller's live counters at the moment a read completes. */
export interface SessionReadOwnership {
	destroyed: boolean;
	presetId: string | null;
	mode: string | null;
	generation: number;
	/** Newest seq issued for the completing read's kind. */
	newestIssuedSeq: number;
}

export function sessionReadOwnsState(
	token: SessionReadToken,
	current: SessionReadOwnership
): boolean {
	if (current.destroyed) return false;
	if (token.presetId !== current.presetId) return false;
	if (token.mode !== current.mode) return false;
	if (token.generation !== null && token.generation !== current.generation) return false;
	return token.seq === current.newestIssuedSeq;
}

interface ApiResult<T> {
	success: boolean;
	data?: T;
	error?: string;
}

export interface SessionControllerApi {
	getSessionsForPreset(presetId: string): Promise<ApiResult<Session[]>>;
	getSessionById(sessionId: string): Promise<ApiResult<Session>>;
	saveSession(request: {
		preset_id: string;
		name: string;
		data: ModeBasedSessionData;
	}): Promise<ApiResult<Session>>;
	updateSession(
		sessionId: string,
		request: { name: string; data: ModeBasedSessionData }
	): Promise<ApiResult<Session>>;
	deleteSession(sessionId: string): Promise<ApiResult<{ message: string }>>;
	getSessionVersions(sessionId: string): Promise<ApiResult<SessionVersionSummary[]>>;
	getSessionVersion(
		sessionId: string,
		versionNumber: number
	): Promise<ApiResult<SessionVersionDetail>>;
}

export interface SessionControllerTabs {
	subscribe(run: (value: { tabs: Tab[] }) => void): () => void;
	updateTab(tabId: string, updates: Partial<Tab>): void;
}

export interface SessionControllerStorage {
	get(key: string): string | null;
	set(key: string, value: string): void;
}

export interface SessionControllerToasts {
	info(message: string): void;
	warning(message: string): void;
	error(message: string): void;
}

export interface SessionControllerLogger {
	warn(...args: unknown[]): void;
	error(...args: unknown[]): void;
}

/** Injected so a test can run the autosave cadence and the list-load retry on
 *  a fake clock instead of real time. */
export interface SessionControllerTimers {
	setInterval(handler: () => void, ms: number): number;
	clearInterval(id: number): void;
	setTimeout(handler: () => void, ms: number): number;
	clearTimeout(id: number): void;
}

export interface SessionControllerDeps {
	api: SessionControllerApi;
	tabs: SessionControllerTabs;
	storage: SessionControllerStorage;
	toasts: SessionControllerToasts;
	logger: SessionControllerLogger;
	timers?: SessionControllerTimers;
	/** Wall clock for `lastSavedTime`. */
	now?: () => Date;
	/** Coalesces state derivation the way Svelte's own flush does; a test can
	 *  make it run inline. */
	schedule?: (run: () => void) => void;
}

/** What the view (`tabId`/`presetId`/`currentMode`) currently represents. */
export interface SessionControllerContext {
	tabId: string;
	presetId: string | null;
	currentMode: string | null;
	presetVersion?: string;
	/** The current mode's variants, already sorted — used to fall back to the
	 *  mode's default when a restored variant no longer exists. */
	modeVariants: PresetModeVariant[];
}

export interface SessionControllerState {
	sessions: Session[];
	selectedSessionId: string;
	currentSession: Session | null;
	hasUnsavedChanges: boolean;
	lastSavedTime: Date | null;
	isSessionLoading: boolean;
	isSaving: boolean;
	isQuickSaving: boolean;
	error: string | null;
	nameError: string;
	historySessionId: string | null;
	historyVersions: SessionVersionSummary[];
	isHistoryLoading: boolean;
	historyError: string | null;
	isRestoringVersion: boolean;
	autoSaveEnabled: boolean;
	autoSaveInterval: number;
	sessionControlsEnabled: boolean;
}

export interface SessionController {
	state: Readable<SessionControllerState>;
	setContext(next: SessionControllerContext): void;
	start(): void;
	destroy(): void;
	loadSessions(): Promise<void>;
	select(sessionId: string): Promise<void>;
	/** Resolves false when there is nothing to quick-save (no session yet) and
	 *  the view should offer its save-as modal instead. */
	quickSave(): Promise<boolean>;
	/** `mode` mirrors SessionSaveModal's: 'rename' updates the selected
	 *  session, 'save-as' always creates a new one. Resolves true when the
	 *  caller should close its modal. */
	saveAs(name: string, mode: 'rename' | 'save-as'): Promise<boolean>;
	deleteSession(): Promise<boolean>;
	openHistory(sessionId: string): Promise<void>;
	closeHistory(): void;
	restoreVersion(sessionId: string, versionNumber: number): Promise<void>;
	startAutosave(): void;
	stopAutosave(): void;
	toggleAutosave(): void;
	setAutosaveInterval(interval: number): void;
	/** Opening a save/rename modal clears only the name error: a save error
	 *  raised by the previous attempt stays visible until the modal is closed. */
	clearNameError(): void;
	clearFeedback(): void;
}

const DEFAULT_AUTOSAVE_INTERVAL = 10000;
const RETRY_BASE_DELAY = 2000;
const RETRY_MAX_DELAY = 30000;

const defaultTimers: SessionControllerTimers = {
	setInterval: (handler, ms) => setInterval(handler, ms) as unknown as number,
	clearInterval: (id) => clearInterval(id),
	setTimeout: (handler, ms) => setTimeout(handler, ms) as unknown as number,
	clearTimeout: (id) => clearTimeout(id)
};

export function createSessionController(deps: SessionControllerDeps): SessionController {
	const timers = deps.timers ?? defaultTimers;
	const now = deps.now ?? (() => new Date());
	const schedule = deps.schedule ?? ((run: () => void) => void Promise.resolve().then(run));

	let ctx: SessionControllerContext = {
		tabId: '',
		presetId: null,
		currentMode: null,
		presetVersion: undefined,
		modeVariants: []
	};

	let started = false;
	let destroyed = false;

	let tabs: Tab[] = [];
	let currentTabData: Tab | undefined;

	let sessions: Session[] = [];
	let selectedSessionId = '';
	let currentSession: Session | null = null;
	let hasUnsavedChanges = false;
	let savedSessionSignature: string | null = null;
	let currentSessionSignature: string | null = null;
	let lastSavedTime: Date | null = null;
	// `isSessionLoading` and `error` are published as one field each but are
	// raised by three unrelated operations (the preset list, a selection
	// hydration, a delete). Kept apart here and combined only at publication:
	// a list refresh finishing must not tell the view the selection it is still
	// hydrating has arrived, and an older list failure must not stand in front
	// of the error a newer selection or save raised.
	let isListLoading = false;
	let listLoadInFlight: SessionReadToken | null = null;
	let isSelectionLoading = false;
	let selectionLoadInFlight: SessionReadToken | null = null;
	let isDeleteLoading = false;
	let listError: string | null = null;
	let selectionError: string | null = null;
	let isSaving = false;
	let isQuickSaving = false;
	let error: string | null = null;
	let nameError = '';

	let historySessionId: string | null = null;
	let historyVersions: SessionVersionSummary[] = [];
	let isHistoryLoading = false;
	let historyError: string | null = null;
	let isRestoringVersion = false;

	let autoSaveEnabled = false;
	let autoSaveInterval = DEFAULT_AUTOSAVE_INTERVAL;
	let autoSaveIntervalId: number | null = null;

	// `commandGeneration` moves on every selection/tab/preset/mode change and on
	// destroy; `quickSaveInFlight` keeps the saving flag owned by the newest
	// quick save rather than the first to finish. See the ownership rule at the
	// top of this file for what the seq counters add on top.
	let commandGeneration = 0;
	// One handle per kind of operation a view puts a dialog or a busy control
	// behind. The handle names the operation the view is currently waiting on,
	// so an older one can neither release the newer one's busy flag nor answer
	// for the dialog now on screen.
	let quickSaveInFlight: SessionCommandToken | null = null;
	let saveAsInFlight: SessionCommandToken | null = null;
	let deleteInFlight: SessionCommandToken | null = null;
	let restoreInFlight: SessionCommandToken | null = null;
	let commandSeq = 0;
	let lastAppliedSeq = 0;
	const newestIssuedSeq = new Map<SessionCommandIntent, number>();
	// Bumped only by a context change (tab/preset/mode), never by a selection
	// change: the sessions list and the view's save dialog belong to the
	// context, not to whichever session is picked inside it.
	let listGeneration = 0;

	// Read ordering, kept apart from the command counters above: a read is
	// superseded by the next read of ITS kind, not by a save.
	let readSeq = 0;
	const newestIssuedReadSeq = new Map<SessionReadKind, number>();

	// The views keep ONE instance alive across tab switches and only swap
	// `tabId`, so a switch is not a remount. Without tracking which tab the
	// local state represents, a `tabId` change is indistinguishable from picking
	// a session on the same tab, and the fetch below overwrites the tab's draft
	// with server data.
	let adoptedTabId: string | null = null;

	// The list-load trigger (presetId/currentMode) never fires again on its own
	// after a backend outage, so without a retry the view can never resolve
	// currentSession for a tab's persisted selectedSessionId.
	let loadSessionsRetryTimer: number | null = null;
	let loadSessionsRetryDelay = RETRY_BASE_DELAY;

	let flushScheduled = false;
	let flushing = false;

	/** Any of the three operations that put the session controls in a busy
	 *  state; the tab-link gate below reads the same combination the view does. */
	function sessionBusy(): boolean {
		return isListLoading || isSelectionLoading || isDeleteLoading;
	}

	function snapshot(): SessionControllerState {
		return {
			sessions,
			selectedSessionId,
			currentSession,
			hasUnsavedChanges,
			lastSavedTime,
			isSessionLoading: sessionBusy(),
			isSaving,
			isQuickSaving,
			error: error ?? selectionError ?? listError,
			nameError,
			historySessionId,
			historyVersions,
			isHistoryLoading,
			historyError,
			isRestoringVersion,
			autoSaveEnabled,
			autoSaveInterval,
			sessionControlsEnabled: !!(ctx.presetId && ctx.currentMode)
		};
	}

	const store = writable<SessionControllerState>(snapshot());

	/** Publish now, and derive (tab adoption, hydration, signature, dirty) on a
	 *  microtask — deriving inline would re-enter this pipeline from the middle
	 *  of a command's own store write. */
	function publish() {
		if (destroyed) return;
		store.set(snapshot());
		if (!flushing) scheduleDerive();
	}

	function scheduleDerive() {
		if (destroyed || flushScheduled) return;
		flushScheduled = true;
		schedule(() => {
			flushScheduled = false;
			if (destroyed) return;
			derive();
		});
	}

	/** One topological pass over what the views used to express as `$:` blocks,
	 *  in the order Svelte ran them. */
	function derive() {
		flushing = true;
		try {
			adoptTab();
			evaluateTabLink();
			mirrorSavedSignatureFromTab();
			refreshSignature();
		} finally {
			flushing = false;
		}
		if (!destroyed) store.set(snapshot());
	}

	function readTab(): Tab | undefined {
		return tabs.find((tab) => tab.id === ctx.tabId);
	}

	const unsubscribeTabs = deps.tabs.subscribe((value) => {
		tabs = value.tabs;
		currentTabData = readTab();
		scheduleDerive();
	});

	// Adopt identity/metadata from the store only. Never fetch or write here:
	// the tabs store already holds the tab's draft and it must win.
	function adoptTab() {
		if (!started || ctx.tabId === adoptedTabId) return;
		adoptedTabId = ctx.tabId;
		if (currentTabData?.selectedSessionId) {
			setSelectedSessionId(currentTabData.selectedSessionId);
			savedSessionSignature = currentTabData.savedSessionSignature ?? null;
			currentSession = sessions.find((session) => session.id === selectedSessionId) ?? null;
			lastSavedTime = currentSession ? new Date(currentSession.updated_at) : null;
			hasUnsavedChanges = sessionIsDirty(
				!!currentSession,
				savedSessionSignature,
				currentSessionSignature
			);
		} else {
			setSelectedSessionId('');
			currentSession = null;
			hasUnsavedChanges = false;
			savedSessionSignature = null;
			lastSavedTime = null;
		}
	}

	// Same-tab session change only (picker/history/programmatic load). A switch
	// to a different tab is handled by adoptTab and must not reach this fetch.
	//
	// The busy gate is load-bearing: `select` assigns `selectedSessionId`
	// optimistically BEFORE awaiting its fetch, so mid-flight the local id is the
	// new session while the store still holds the old one. Without this gate
	// `shouldHydrateSessionSelection` reads that mismatch as a store-side change
	// and re-fetches the OLD session over the user's pick.
	function evaluateTabLink() {
		if (sessionBusy() || ctx.tabId !== adoptedTabId || !currentTabData) return;

		if (currentTabData.selectedSessionId) {
			if (
				shouldHydrateSessionSelection(started, selectedSessionId, currentTabData.selectedSessionId)
			) {
				void syncSessionFromTab(currentTabData.selectedSessionId);
			}
		} else if (selectedSessionId) {
			// Tab has no session but this controller does — clear it.
			setSelectedSessionId('');
			currentSession = null;
			hasUnsavedChanges = false;
			savedSessionSignature = null;
			lastSavedTime = null;
			if (
				(currentTabData.savedSessionSignature !== undefined &&
					currentTabData.savedSessionSignature !== null) ||
				currentTabData.sessionBaselineAwaitingFormNormalization
			) {
				deps.tabs.updateTab(ctx.tabId, {
					savedSessionSignature: null,
					sessionBaselineAwaitingFormNormalization: false
				});
			}
		}
	}

	// The page applies this signature only after a genuine server hydration or
	// save. Reading it back preserves the existing dirty indicator without
	// treating the tab's current draft as a new saved baseline.
	function mirrorSavedSignatureFromTab() {
		if (
			currentTabData &&
			currentTabData.savedSessionSignature !== undefined &&
			currentTabData.savedSessionSignature !== savedSessionSignature
		) {
			savedSessionSignature = currentTabData.savedSessionSignature;
		}
	}

	function refreshSignature() {
		currentSessionSignature =
			currentTabData && ctx.currentMode ? JSON.stringify(collectCurrentSessionData()) : null;
		if (currentSession && currentSessionSignature !== null) {
			hasUnsavedChanges = sessionIsDirty(true, savedSessionSignature, currentSessionSignature);
		}
	}

	function setSelectedSessionId(sessionId: string) {
		if (selectedSessionId === sessionId) return;
		selectedSessionId = sessionId;
		commandGeneration += 1;
	}

	function beginSessionCommand(intent: SessionCommandIntent): SessionCommandToken {
		commandSeq += 1;
		newestIssuedSeq.set(intent, commandSeq);
		return {
			sessionId: selectedSessionId,
			generation: commandGeneration,
			seq: commandSeq,
			listGeneration,
			intent
		};
	}

	function ownsActiveState(command: SessionCommandToken): boolean {
		return sessionCommandOwnsActiveState(command, {
			generation: commandGeneration,
			activeSessionId: selectedSessionId,
			newestIssuedSeq: newestIssuedSeq.get(command.intent) ?? 0,
			lastAppliedSeq
		});
	}

	/** The sessions list and the view's save dialog still belong to the context
	 *  this command started under. */
	function ownsSessionList(command: SessionCommandToken): boolean {
		return command.listGeneration === listGeneration;
	}

	/** Whether this command may still answer for the view: close its dialog,
	 *  raise its toast, own its feedback. It must be the operation the view is
	 *  waiting on (a newer one of the same kind has taken the handle otherwise),
	 *  under a context still on screen, on a controller still alive. Releasing a
	 *  busy flag is deliberately NOT gated on this — see the finally blocks. */
	function ownsDialog(
		command: SessionCommandToken,
		inFlight: SessionCommandToken | null
	): boolean {
		if (destroyed) return false;
		if (inFlight !== command) return false;
		return ownsSessionList(command);
	}

	function beginSessionRead(
		kind: SessionReadKind,
		targetId: string,
		options: { ownsSelection: boolean }
	): SessionReadToken {
		readSeq += 1;
		newestIssuedReadSeq.set(kind, readSeq);
		return {
			kind,
			seq: readSeq,
			tabId: ctx.tabId,
			presetId: ctx.presetId,
			mode: ctx.currentMode,
			generation: options.ownsSelection ? commandGeneration : null,
			targetId
		};
	}

	function ownsRead(read: SessionReadToken): boolean {
		return sessionReadOwnsState(read, {
			destroyed,
			presetId: ctx.presetId,
			mode: ctx.currentMode,
			generation: commandGeneration,
			newestIssuedSeq: newestIssuedReadSeq.get(read.kind) ?? 0
		});
	}

	/** Retires whatever read of this kind is in flight, so its completion can no
	 *  longer publish. */
	function retireReads(kind: SessionReadKind) {
		readSeq += 1;
		newestIssuedReadSeq.set(kind, readSeq);
	}

	/** Records that the active state now reflects this command, so anything
	 *  issued earlier can no longer overwrite it. */
	function markApplied(command: SessionCommandToken) {
		lastAppliedSeq = command.seq;
	}

	/** A load, restore or any other direct write of the active state takes the
	 *  same ordering slot a command would, so a save issued before it cannot
	 *  land on top of it afterwards. */
	function markAppliedDirectly() {
		commandSeq += 1;
		lastAppliedSeq = commandSeq;
	}

	/**
	 * Composes the FULL multi-mode save payload: the active mode's fresh live
	 * snapshot, plus every other mode this tab has visited (from
	 * `modeStateByMode`) overlaid onto the session's last-saved baseline — so
	 * saving captures every mode the user configured this session, not just
	 * whichever one happens to be active when they hit Save.
	 */
	function collectCurrentSessionData(): ModeBasedSessionData {
		return collectTabSessionData(
			currentTabData,
			ctx.currentMode,
			currentSession?.data || {},
			ctx.presetVersion
		);
	}

	function recordSavedBaseline(signature: string | null, awaitingFormNormalization = false) {
		savedSessionSignature = signature;
		deps.tabs.updateTab(ctx.tabId, {
			savedSessionSignature: signature,
			sessionBaselineAwaitingFormNormalization: awaitingFormNormalization
		});
	}

	function clearActiveSession() {
		setSelectedSessionId('');
		currentSession = null;
		hasUnsavedChanges = false;
		recordSavedBaseline(null);
		lastSavedTime = null;
	}

	/** Non-blocking notice when the session's saved `presetVersion` no longer
	 *  matches the live preset — the session may reference form fields/defaults
	 *  that have since changed. Never blocks loading the session. */
	function warnIfPresetVersionDrifted(sessionData: ModeBasedSessionData, mode: string | null) {
		const savedVersion = mode ? sessionData[mode]?.presetVersion : undefined;
		if (savedVersion && ctx.presetVersion && savedVersion !== ctx.presetVersion) {
			deps.toasts.warning(
				`This session was saved with preset version ${savedVersion}, now at ${ctx.presetVersion} — some fields may have changed.`
			);
		}
	}

	function applySessionLayout(sessionData: ModeBasedSessionData, mode: string | null) {
		const modeData = mode ? sessionData[mode] : undefined;
		const layout = modeData?.layoutMode;
		const updates: {
			layoutMode?: GenerationLayoutMode;
			leftPanelCollapsed?: boolean;
			workbenchCollapsed?: boolean;
		} = {};
		if (layout === 'two' || layout === 'three') {
			updates.layoutMode = layout;
		}
		if (typeof modeData?.leftPanelCollapsed === 'boolean') {
			updates.leftPanelCollapsed = modeData.leftPanelCollapsed;
		}
		if (typeof modeData?.workbenchCollapsed === 'boolean') {
			updates.workbenchCollapsed = modeData.workbenchCollapsed;
		}
		if (Object.keys(updates).length > 0) deps.tabs.updateTab(ctx.tabId, updates);
	}

	async function syncSessionFromTab(sessionId: string) {
		if (!sessionId || selectedSessionId === sessionId) return;

		setSelectedSessionId(sessionId);
		const read = beginSessionRead('session', sessionId, { ownsSelection: true });

		try {
			const response = await deps.api.getSessionById(read.targetId);
			// A hydration that outlived its selection or its preset answers for
			// nothing: neither its payload nor its "this session is gone" verdict
			// describes what is on screen now.
			if (!ownsRead(read)) return;

			if (response.success && response.data) {
				// Verify this session belongs to the current preset.
				if (response.data.preset_id !== read.presetId) {
					deps.logger.warn('[Session] Session belongs to different preset, clearing it');
					clearActiveSession();
					deps.tabs.updateTab(read.tabId, {
						selectedSessionId: null,
						savedSessionSignature: null
					});
				} else {
					// A change after mount is an explicit programmatic selection, unlike
					// a remount. Apply the full server payload through the same path as
					// the picker so prompt/form/layout state and the saved baseline agree.
					await applySessionModeData(read.targetId, response.data.data, response.data, {
						markSaved: true
					});
				}
			} else if (isSessionMissingResponse(response)) {
				deps.logger.warn('[Session] Session no longer exists, clearing it');
				clearActiveSession();
				deps.tabs.updateTab(read.tabId, { selectedSessionId: null, savedSessionSignature: null });
			}
		} catch (err) {
			// A thrown HTTP 404 proves the session is gone; anything else (backend
			// unreachable/restarting) must leave the tab's link intact so the list
			// load's retry can still bind currentSession once the backend answers.
			const owned = ownsRead(read);
			if (isSessionGoneError(err)) {
				deps.logger.error('Failed to sync session from tab:', err);
				if (!owned) return;
				clearActiveSession();
				deps.tabs.updateTab(read.tabId, { selectedSessionId: null, savedSessionSignature: null });
			} else {
				deps.logger.warn(
					'[Session] Backend unreachable while syncing session, keeping the saved link:',
					err
				);
				if (!owned) return;
			}
		}
		publish();
	}

	/**
	 * Apply one mode's session data to this tab — the single code path used to
	 * load both a normal session and a historical version of one (see `select`
	 * and `restoreVersion`). `modeBasedData` is the mode-keyed payload (a
	 * session's `.data`, or a history version's `.data`); `sessionMeta` is the
	 * (always current) Session record used for identity/metadata. When
	 * `markSaved` is true, the just-applied data becomes the new "no unsaved
	 * changes" baseline (a normal load). When false (a historical restore), the
	 * baseline is left pointing at nothing so the tab reads as having unsaved
	 * changes until the user hits Save — restoring a save never silently becomes
	 * "the latest" on its own.
	 */
	async function applySessionModeData(
		sessionId: string,
		modeBasedData: ModeBasedSessionData,
		sessionMeta: Session,
		options: { markSaved: boolean }
	) {
		// If the payload has no data for the current mode, load an empty state
		// for this mode — the user can then fill it in and save, accumulating
		// per-mode data on the same session.
		const modeData: SessionData = (ctx.currentMode && modeBasedData[ctx.currentMode]) || {};

		// Fall back to the mode's default variant, non-fatally, if the saved
		// variant no longer exists (e.g. the preset dropped/renamed a form).
		const restoredVariant = resolveVariant(ctx.modeVariants, modeData.selectedVariant ?? null);
		const restoredModeState = seedModeStateFromSessionData(modeBasedData, ctx.currentMode);
		const restoredPatch = buildSessionRestoreTabPatch(modeData, {
			selectedBackendId: currentTabData?.selectedBackendId,
			promptPanelWidth: currentTabData?.promptPanelWidth
		});
		const restoredTab = currentTabData && {
			...currentTabData,
			selectedSessionId: sessionId,
			selectedVariant: restoredVariant,
			...restoredPatch,
			modeStateByMode: restoredModeState
		};
		// Set the raw server baseline and pending flag in the same store write as
		// an explicit load. A cached schema can normalize before the next tick;
		// the first DynamicForm publication then updates only formData's baseline.
		const restoredBaseline = options.markSaved
			? JSON.stringify(
					collectTabSessionData(
						restoredTab || undefined,
						ctx.currentMode,
						sessionMeta.data,
						ctx.presetVersion
					)
				)
			: null;

		deps.tabs.updateTab(ctx.tabId, {
			// Don't override preset — the user is already on the correct preset.
			selectedSessionId: sessionId,
			selectedVariant: restoredVariant,
			...restoredPatch,
			// Seed the per-mode cache from every OTHER mode this session has data
			// for, so switching modes right after this load restores that mode's
			// saved config instead of starting empty.
			modeStateByMode: restoredModeState,
			...(options.markSaved
				? {
						savedSessionSignature: restoredBaseline,
						sessionBaselineAwaitingFormNormalization: true
					}
				: {})
		});

		markAppliedDirectly();
		setSelectedSessionId(sessionId);
		currentSession = sessionMeta;
		if (options.markSaved) savedSessionSignature = restoredBaseline;
		lastSavedTime = new Date(sessionMeta.updated_at);
		applySessionLayout(modeBasedData, ctx.currentMode);
		warnIfPresetVersionDrifted(modeBasedData, ctx.currentMode);
		publish();
		await Promise.resolve();

		if (options.markSaved) {
			hasUnsavedChanges = false;
		} else {
			// Null out the baseline rather than pointing it at the session's
			// actual latest data: the dirty diff would otherwise have to exactly
			// re-derive collectCurrentSessionData()'s field defaulting to avoid a
			// phantom-dirty flag. Leaving it null just pauses that diff — the next
			// real save recomputes it and hasUnsavedChanges resumes tracking.
			recordSavedBaseline(null);
			hasUnsavedChanges = true;
		}
		publish();
	}

	async function loadSessions() {
		if (!ctx.presetId) return;

		if (loadSessionsRetryTimer !== null) {
			timers.clearTimeout(loadSessionsRetryTimer);
			loadSessionsRetryTimer = null;
		}

		const read = beginSessionRead('list', '', { ownsSelection: false });
		listLoadInFlight = read;
		// The active-state slot as it stood when this load was issued. A list row
		// is only a summary of the session; a selection hydration or a save that
		// lands first knows more about it, and claims the slot by advancing this.
		const appliedSeqAtIssue = lastAppliedSeq;

		try {
			isListLoading = true;
			listError = null;
			publish();
			const response = await deps.api.getSessionsForPreset(read.presetId!);
			if (!ownsRead(read)) return;
			if (response.success && response.data) {
				sessions = response.data;
				const sessionForTab =
					response.data.find((session) => session.id === selectedSessionId) ?? null;
				if (sessionForTab && lastAppliedSeq === appliedSeqAtIssue) {
					currentSession = sessionForTab;
					lastSavedTime = new Date(sessionForTab.updated_at);
					hasUnsavedChanges = sessionIsDirty(true, savedSessionSignature, currentSessionSignature);
				}
			}
			loadSessionsRetryDelay = RETRY_BASE_DELAY;
		} catch (err) {
			deps.logger.error('Failed to load sessions:', err);
			// The retry belongs to the preset the failed load was for, and to a
			// live controller: an obsolete or post-teardown rejection must not
			// raise an error the user can no longer act on, nor install a timer
			// that outlives the view.
			if (!ownsRead(read)) return;
			listError = err instanceof Error ? err.message : 'Failed to load sessions';
			loadSessionsRetryTimer = timers.setTimeout(() => {
				loadSessionsRetryTimer = null;
				void loadSessions();
			}, loadSessionsRetryDelay);
			loadSessionsRetryDelay = Math.min(loadSessionsRetryDelay * 2, RETRY_MAX_DELAY);
		} finally {
			// Identity, not ownership: a load retired by a context switch still has
			// to put down the flag it raised, and it owns no other operation's.
			if (listLoadInFlight === read) {
				listLoadInFlight = null;
				isListLoading = false;
			}
			publish();
		}
	}

	async function select(sessionId: string) {
		if (!sessionId) return;

		isSelectionLoading = true;
		selectionError = null;
		setSelectedSessionId(sessionId);
		const read = beginSessionRead('session', sessionId, { ownsSelection: true });
		selectionLoadInFlight = read;
		publish();

		try {
			const response = await deps.api.getSessionById(read.targetId);
			// Two picks in a row resolve in whatever order the backend answers:
			// the older one must not hydrate its session back over the newer pick.
			if (!ownsRead(read)) return;
			if (response.success && response.data) {
				await applySessionModeData(read.targetId, response.data.data, response.data, {
					markSaved: true
				});
			}
		} catch (err) {
			deps.logger.error('Failed to load session:', err);
			if (!ownsRead(read)) return;
			selectionError = err instanceof Error ? err.message : 'Failed to load session';
		} finally {
			if (selectionLoadInFlight === read) {
				selectionLoadInFlight = null;
				isSelectionLoading = false;
			}
			publish();
		}
	}

	function startAutosave() {
		stopAutosave();
		autoSaveIntervalId = timers.setInterval(() => {
			if (currentSession && hasUnsavedChanges && !isQuickSaving) {
				void performAutoSave();
			}
		}, autoSaveInterval);
	}

	function stopAutosave() {
		if (autoSaveIntervalId !== null) {
			timers.clearInterval(autoSaveIntervalId);
			autoSaveIntervalId = null;
		}
	}

	function toggleAutosave() {
		autoSaveEnabled = !autoSaveEnabled;
		deps.storage.set('autoSaveEnabled', autoSaveEnabled.toString());

		if (autoSaveEnabled) {
			startAutosave();
		} else {
			stopAutosave();
		}
		publish();
	}

	function setAutosaveInterval(newInterval: number) {
		autoSaveInterval = newInterval;
		deps.storage.set('autoSaveInterval', newInterval.toString());

		if (autoSaveEnabled) {
			startAutosave();
		}
		publish();
	}

	async function performAutoSave() {
		if (!currentSession || !ctx.presetId || isQuickSaving) return;

		// Safety check: ensure the session belongs to the current preset.
		if (currentSession.preset_id !== ctx.presetId) {
			deps.logger.error('[AutoSave] Session belongs to different preset, aborting auto-save');
			stopAutosave();
			clearActiveSession();
			publish();
			return;
		}

		const savingSession = currentSession;
		const command = beginSessionCommand('autosave');
		quickSaveInFlight = command;

		try {
			isQuickSaving = true;
			const sessionData = collectCurrentSessionData();
			publish();

			const response = await deps.api.updateSession(savingSession.id, {
				name: savingSession.name,
				data: sessionData
			});

			if (response.success && response.data) {
				sessions = sessions.map((s) => (s.id === savingSession.id ? response.data! : s));
				if (ownsActiveState(command)) {
					markApplied(command);
					currentSession = response.data;
					recordSavedBaseline(JSON.stringify(sessionData));
					hasUnsavedChanges = false;
					lastSavedTime = now();
				}
			}
		} catch (err) {
			deps.logger.error('Auto-save failed:', err);
		} finally {
			if (quickSaveInFlight === command) {
				quickSaveInFlight = null;
				isQuickSaving = false;
			}
			publish();
		}
	}

	async function quickSave(): Promise<boolean> {
		if (!currentSession || !ctx.presetId) return false;

		// Safety check: ensure the session belongs to the current preset.
		if (currentSession.preset_id !== ctx.presetId) {
			error = 'Cannot save: Session belongs to a different preset';
			deps.logger.error('[QuickSave] Session belongs to different preset, aborting');
			clearActiveSession();
			publish();
			return true;
		}

		const savingSession = currentSession;
		const command = beginSessionCommand('quick-save');
		quickSaveInFlight = command;

		try {
			isQuickSaving = true;
			error = null;

			const sessionData = collectCurrentSessionData();
			publish();

			const response = await deps.api.updateSession(savingSession.id, {
				name: savingSession.name,
				data: sessionData
			});

			if (response.success && response.data) {
				sessions = sessions.map((s) => (s.id === savingSession.id ? response.data! : s));
				if (ownsActiveState(command)) {
					markApplied(command);
					currentSession = response.data;
					recordSavedBaseline(JSON.stringify(sessionData));
					hasUnsavedChanges = false;
					lastSavedTime = now();
				}
			}
		} catch (err) {
			if (ownsActiveState(command)) {
				markApplied(command);
				error = err instanceof Error ? err.message : 'Failed to save session';
			}
			deps.logger.error('Failed to quick save:', err);
		} finally {
			if (quickSaveInFlight === command) {
				quickSaveInFlight = null;
				isQuickSaving = false;
			}
			publish();
		}

		return true;
	}

	async function saveAs(name: string, mode: 'rename' | 'save-as'): Promise<boolean> {
		const isSaveAs = mode === 'save-as';

		if (!name.trim()) {
			nameError = 'Session name is required';
			publish();
			return false;
		}

		const existingSession = sessions.find(
			(s) => s.name === name.trim() && (isSaveAs || s.id !== selectedSessionId)
		);

		if (existingSession) {
			nameError = 'A session with this name already exists';
			publish();
			return false;
		}

		const command = beginSessionCommand('save-as');
		saveAsInFlight = command;
		let closed = false;

		try {
			isSaving = true;
			error = null;
			publish();

			const sessionData = collectCurrentSessionData();

			if (!isSaveAs && command.sessionId) {
				const response = await deps.api.updateSession(command.sessionId, {
					name: name.trim(),
					data: sessionData
				});

				if (response.success && response.data) {
					// Safe under any context: an id absent from the current list is
					// simply not matched.
					sessions = sessions.map((s) => (s.id === command.sessionId ? response.data! : s));
					if (ownsActiveState(command)) {
						markApplied(command);
						currentSession = response.data;
						recordSavedBaseline(JSON.stringify(sessionData));
						hasUnsavedChanges = false;
						lastSavedTime = now();
					}
				}
			} else {
				const response = await deps.api.saveSession({
					preset_id: ctx.presetId!,
					name: name.trim(),
					data: sessionData
				});

				if (response.success && response.data) {
					// A record created under another preset/tab/mode belongs to that
					// list, not to whichever one is on screen now.
					if (ownsSessionList(command)) {
						sessions = [response.data, ...sessions];
					}
					if (ownsActiveState(command)) {
						markApplied(command);
						setSelectedSessionId(response.data.id);
						currentSession = response.data;
						recordSavedBaseline(JSON.stringify(sessionData));
						hasUnsavedChanges = false;
						lastSavedTime = now();

						deps.tabs.updateTab(ctx.tabId, { selectedSessionId: response.data.id });
					}
				}
			}

			// Answering for the dialog is only this command's business while the
			// context it was opened under is still the one on screen, and while
			// no later save has taken over the dialog the view is showing.
			closed = ownsDialog(command, saveAsInFlight);
		} catch (err) {
			if (ownsActiveState(command)) {
				markApplied(command);
				nameError = err instanceof Error ? err.message : 'Failed to save session';
			}
			deps.logger.error('Failed to save session:', err);
		} finally {
			// Identity only: a save whose context has moved on still has to put
			// down the flag it raised, or the control stays busy for good.
			if (saveAsInFlight === command) {
				saveAsInFlight = null;
				isSaving = false;
			}
			publish();
		}

		return closed;
	}

	async function deleteSession(): Promise<boolean> {
		if (!selectedSessionId) return false;

		const command = beginSessionCommand('delete');
		deleteInFlight = command;
		let deleted = false;

		try {
			isDeleteLoading = true;
			publish();

			await deps.api.deleteSession(command.sessionId);

			sessions = sessions.filter((s) => s.id !== command.sessionId);
			if (ownsActiveState(command)) {
				markApplied(command);
				clearActiveSession();
				deps.tabs.updateTab(ctx.tabId, { selectedSessionId: null, savedSessionSignature: null });
			}

			// The record is gone from the server either way; what is scoped here
			// is the confirmation dialog the view should close.
			deleted = ownsDialog(command, deleteInFlight);
		} catch (err) {
			if (ownsDialog(command, deleteInFlight)) {
				deps.toasts.error(err instanceof Error ? err.message : 'Failed to delete session');
			}
			deps.logger.error('Failed to delete session:', err);
		} finally {
			if (deleteInFlight === command) {
				deleteInFlight = null;
				isDeleteLoading = false;
			}
			publish();
		}

		return deleted;
	}

	// Session history: list this session's past saves.
	async function openHistory(sessionId: string) {
		const read = beginSessionRead('history', sessionId, { ownsSelection: false });
		historySessionId = sessionId;
		historyVersions = [];
		historyError = null;
		isHistoryLoading = true;
		publish();

		try {
			const response = await deps.api.getSessionVersions(read.targetId);
			if (!ownsRead(read)) return;
			if (response.success && response.data) {
				historyVersions = response.data;
			}
		} catch (err) {
			deps.logger.error('Failed to load session history:', err);
			if (!ownsRead(read)) return;
			historyError = 'Could not load history for this session.';
		} finally {
			if (ownsRead(read)) {
				isHistoryLoading = false;
				publish();
			}
		}
	}

	function closeHistory() {
		// Closing retires the load: its rows and its error belong to a panel that
		// is no longer open, and its spinner would otherwise be left raised.
		retireReads('history');
		historySessionId = null;
		historyVersions = [];
		historyError = null;
		isHistoryLoading = false;
		publish();
	}

	// One-click restore: load a past save into this tab through the exact same
	// path a normal session load uses (applySessionModeData above). There is no
	// restore endpoint — the next normal Save is what makes this the latest.
	async function restoreVersion(sessionId: string, versionNumber: number) {
		const sessionMeta = sessions.find((s) => s.id === sessionId);
		if (!sessionMeta) {
			deps.toasts.error('That session is no longer available.');
			return;
		}

		const command = beginSessionCommand('restore');
		restoreInFlight = command;

		try {
			isRestoringVersion = true;
			publish();
			const response = await deps.api.getSessionVersion(sessionId, versionNumber);
			if (response.success && response.data && ownsActiveState(command)) {
				markApplied(command);
				const version = response.data;
				await applySessionModeData(sessionId, version.data, sessionMeta, { markSaved: false });
				closeHistory();
				deps.toasts.info(
					`Loaded the save from ${timeAgo(version.created_at)} — Save to make it the latest.`
				);
			}
		} catch (err) {
			deps.logger.error('Failed to restore session version:', err);
			if (ownsDialog(command, restoreInFlight)) {
				deps.toasts.error('Could not load that save.');
			}
		} finally {
			if (restoreInFlight === command) {
				restoreInFlight = null;
				isRestoringVersion = false;
			}
			publish();
		}
	}

	function clearNameError() {
		nameError = '';
		publish();
	}

	function clearFeedback() {
		nameError = '';
		error = null;
		selectionError = null;
		listError = null;
		publish();
	}

	function setContext(next: SessionControllerContext) {
		const tabChanged = next.tabId !== ctx.tabId;
		const presetChanged = next.presetId !== ctx.presetId;
		const modeChanged = next.currentMode !== ctx.currentMode;

		ctx = { ...next };
		if (tabChanged) currentTabData = readTab();
		if (tabChanged || presetChanged || modeChanged) {
			commandGeneration += 1;
			listGeneration += 1;
		}

		if ((presetChanged || modeChanged) && ctx.presetId && ctx.currentMode) {
			void loadSessions();
		}
		publish();
	}

	function start() {
		if (started || destroyed) return;
		started = true;

		const savedEnabled = deps.storage.get('autoSaveEnabled');
		const savedInterval = deps.storage.get('autoSaveInterval');

		if (savedEnabled !== null) {
			autoSaveEnabled = savedEnabled === 'true';
		}
		if (savedInterval !== null) {
			autoSaveInterval = parseInt(savedInterval, 10) || DEFAULT_AUTOSAVE_INTERVAL;
		}

		if (autoSaveEnabled) {
			startAutosave();
		}

		// Adopting this tab's session metadata (without fetching) is the same
		// path a later tabId change takes, so a fresh mount and a tab switch on a
		// shared instance can't drift apart.
		currentTabData = readTab();
		derive();
	}

	function destroy() {
		if (destroyed) return;
		destroyed = true;
		stopAutosave();
		commandGeneration += 1;
		if (loadSessionsRetryTimer !== null) {
			timers.clearTimeout(loadSessionsRetryTimer);
			loadSessionsRetryTimer = null;
		}
		unsubscribeTabs();
	}

	return {
		state: { subscribe: store.subscribe },
		setContext,
		start,
		destroy,
		loadSessions,
		select,
		quickSave,
		saveAs,
		deleteSession,
		openHistory,
		closeHistory,
		restoreVersion,
		startAutosave,
		stopAutosave,
		toggleAutosave,
		setAutosaveInterval,
		clearNameError,
		clearFeedback
	};
}
