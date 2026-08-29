<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy, tick } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import {
		api,
		type Session,
		type SessionData,
		type ModeBasedSessionData,
		type SessionVersionSummary
	} from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import type { GenerationLayoutMode } from '$lib/stores/generationLayout';
	import type { PresetModeVariant } from '$lib/types/api';
	import { resolveVariant, sortVariants } from '$lib/utils/variants';
	import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
	import { seedModeStateFromSessionData } from '$lib/utils/modeState';
	import {
		collectTabSessionData,
		isSessionGoneError,
		isSessionMissingResponse,
		sessionIsDirty,
		shouldHydrateSessionSelection
	} from '$lib/utils/sessionTabState';
	import { toasts } from '$lib/stores/toast';
	import { timeAgo } from '$lib/utils/relativeTime';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import SessionSaveModal from '$lib/components/session/SessionSaveModal.svelte';

	// The session half of the old PresetSessionBar, re-homed as the
	// compact pill in the tabs row (top-right). Preset selection/mode/variant
	// state stays in PresetHeader - this component only knows presetId,
	// currentMode and tabId, all sourced from the active tab.
	export let presetId: string | null = null;
	export let currentMode: string | null = null;
	export let tabId: string;
	// Live preset's version, for the saved-session drift warning and the
	// stamp written into new saves - the full preset list itself belongs to
	// PresetHeader, not here.
	export let presetVersion: string | undefined = undefined;
	// Only used to resolve a restored session's variant against what the
	// current mode actually offers (falls back to the mode's default variant
	// if the saved one no longer exists) - the variant *selector* itself is
	// PresetHeader's.
	export let availableModes: Array<{ id: string; variants?: PresetModeVariant[] }> = [];

	$: currentModeVariants = sortVariants(availableModes.find((mode) => mode.id === currentMode)?.variants);

	// Session State
	let isClient = false;
	let sessions: Session[] = [];
	let selectedSessionId: string = '';
	let currentSession: Session | null = null;
	let hasUnsavedChanges = false;
	let savedSessionSignature: string | null = null;
	let lastSavedTime: Date | null = null;
	let isSessionLoading = false;
	let isSaving = false;
	let isQuickSaving = false;
	let error: string | null = null;

	// Session history state — which saved session's history panel is open (if
	// any), the versions fetched for it, and whether a restore is in flight.
	let historySessionId: string | null = null;
	let historyVersions: SessionVersionSummary[] = [];
	let isHistoryLoading = false;
	let historyError: string | null = null;
	let isRestoringVersion = false;

	// Modal states
	let showSaveModal = false;
	let showSaveAsModal = false;
	let showDeleteConfirm = false;
	let sessionName = '';
	let nameError = '';

	// Auto-save state
	let autoSaveEnabled = false;
	let autoSaveInterval = 10000;
	let autoSaveIntervalId: number | null = null;

	// Session controls state
	let sessionControlsEnabled = false;

	// Retry state for loadSessions after a backend outage - the reactive trigger
	// (presetId/currentMode) never fires again on its own, so without a retry the
	// pill can never resolve currentSession for a tab's persisted selectedSessionId.
	let loadSessionsRetryTimer: number | null = null;
	let loadSessionsRetryDelay = 2000;

	/** Non-blocking notice when the session's saved `presetVersion` no longer
	 *  matches the live preset - the session may reference form fields/defaults
	 *  that have since changed. Never blocks loading the session. */
	function warnIfPresetVersionDrifted(sessionData: ModeBasedSessionData, mode: string | null) {
		const savedVersion = mode ? sessionData[mode]?.presetVersion : undefined;
		if (savedVersion && presetVersion && savedVersion !== presetVersion) {
			toasts.warning(
				`This session was saved with preset version ${savedVersion}, now at ${presetVersion} — some fields may have changed.`
			);
		}
	}

	function applySessionLayout(sessionData: ModeBasedSessionData, mode: string | null) {
		const modeData = mode ? sessionData[mode] : undefined;
		const layout = modeData?.layoutMode;
		const updates: { layoutMode?: GenerationLayoutMode; leftPanelCollapsed?: boolean } = {};
		if (layout === 'two' || layout === 'three') {
			updates.layoutMode = layout;
		}
		if (typeof modeData?.leftPanelCollapsed === 'boolean') {
			updates.leftPanelCollapsed = modeData.leftPanelCollapsed;
		}
		if (Object.keys(updates).length > 0) tabsStore.updateTab(tabId, updates);
	}

	// Computed: Check if session controls should be enabled
	// Use currentMode because that's what session management uses
	$: sessionControlsEnabled = !!(presetId && currentMode);

	// Get the specific tab data from the store
	$: currentTabData = $tabsStore.tabs.find((t) => t.id === tabId);

	// TabBar keeps ONE instance alive across tab switches and only swaps `tabId`,
	// so a switch is not a remount. Without tracking which tab the local vars
	// represent, a `tabId` change is indistinguishable from picking a session on
	// the same tab, and the fetch below overwrites the tab's draft with server data.
	let adoptedTabId: string | null = null;

	// Reactive statements - Sessions
	// Load sessions when preset/mode changes
	$: if (presetId && currentMode) {
		loadSessions();
	}

	// Adopt identity/metadata from the store only. Never fetch or write here:
	// tabsStore already holds the tab's draft and it must win.
	$: if (isClient && tabId !== adoptedTabId) {
		adoptedTabId = tabId;
		if (currentTabData?.selectedSessionId) {
			selectedSessionId = currentTabData.selectedSessionId;
			savedSessionSignature = currentTabData.savedSessionSignature ?? null;
			currentSession = sessions.find((session) => session.id === selectedSessionId) ?? null;
			lastSavedTime = currentSession ? new Date(currentSession.updated_at) : null;
			hasUnsavedChanges = sessionIsDirty(!!currentSession, savedSessionSignature, currentSessionSignature);
		} else {
			selectedSessionId = '';
			currentSession = null;
			hasUnsavedChanges = false;
			savedSessionSignature = null;
			lastSavedTime = null;
		}
	}

	// Same-tab session change only (picker/history/programmatic load). A switch to
	// a different tab is handled above and must not reach this fetch.
	//
	// `!isSessionLoading` is load-bearing: handleSessionSelect assigns
	// `selectedSessionId` optimistically BEFORE awaiting its fetch, so mid-flight
	// the local id is the new session while the store still holds the old one.
	// Without this gate `shouldHydrateSessionSelection` reads that mismatch as a
	// store-side change and re-fetches the OLD session over the user's pick.
	$: if (!isSessionLoading && tabId === adoptedTabId && currentTabData?.selectedSessionId) {
		if (shouldHydrateSessionSelection(isClient, selectedSessionId, currentTabData.selectedSessionId)) {
			syncSessionFromTab(currentTabData.selectedSessionId);
		}
	} else if (!isSessionLoading && tabId === adoptedTabId && currentTabData && !currentTabData.selectedSessionId && selectedSessionId) {
		// Tab has no session but component does - clear it
		selectedSessionId = '';
		currentSession = null;
		hasUnsavedChanges = false;
		savedSessionSignature = null;
		lastSavedTime = null;
		if (
			(currentTabData.savedSessionSignature !== undefined && currentTabData.savedSessionSignature !== null) ||
			currentTabData.sessionBaselineAwaitingFormNormalization
		) {
			tabsStore.updateTab(tabId, {
				savedSessionSignature: null,
				sessionBaselineAwaitingFormNormalization: false
			});
		}
	}

	$: currentSessionSignature =
		currentTabData && currentMode ? JSON.stringify(collectCurrentSessionData()) : null;
	$: if (currentSession && currentSessionSignature !== null) {
		hasUnsavedChanges = sessionIsDirty(true, savedSessionSignature, currentSessionSignature);
	}

	// The parent applies this signature only after a genuine server hydration or
	// save. Reading it on every remount preserves the existing dirty indicator
	// without treating the tab's current draft as a new saved baseline.
	$: if (currentTabData && currentTabData.savedSessionSignature !== undefined && currentTabData.savedSessionSignature !== savedSessionSignature) {
		savedSessionSignature = currentTabData.savedSessionSignature;
	}

	onMount(() => {
		isClient = true;

		// Load auto-save preferences
		const savedEnabled = storage.get('autoSaveEnabled');
		const savedInterval = storage.get('autoSaveInterval');

		if (savedEnabled !== null) {
			autoSaveEnabled = savedEnabled === 'true';
		}
		if (savedInterval !== null) {
			autoSaveInterval = parseInt(savedInterval, 10) || 10000;
		}

		if (autoSaveEnabled) {
			startAutoSave();
		}

		// The tab-identity reactive block above adopts this tab's session
		// metadata (without fetching) as soon as `isClient` flips true - same
		// path a later tabId change takes, so a fresh mount and a tab switch
		// on the shared desktop instance can't drift apart.
	});

	onDestroy(() => {
		stopAutoSave();
		if (loadSessionsRetryTimer !== null) clearTimeout(loadSessionsRetryTimer);
	});

	// Session Functions
	async function syncSessionFromTab(sessionId: string) {
		if (!sessionId || selectedSessionId === sessionId) return;

		try {
			selectedSessionId = sessionId;
			const response = await api.getSessionById(sessionId);
			if (response.success && response.data) {
				// Verify this session belongs to the current preset
				if (response.data.preset_id !== presetId) {
					logger.warn('[Session] Session belongs to different preset, clearing it');
					selectedSessionId = '';
					currentSession = null;
					hasUnsavedChanges = false;
					recordSavedBaseline(null);
					lastSavedTime = null;
					// Clear from tab as well
					tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });
					return;
				}

				// A change after mount is an explicit programmatic selection, unlike
				// a remount. Apply the full server payload through the same path as
				// the picker so prompt/form/layout state and the saved baseline agree.
				await applySessionModeData(sessionId, response.data.data, response.data, { markSaved: true });
			} else if (isSessionMissingResponse(response)) {
				logger.warn('[Session] Session no longer exists, clearing it');
				selectedSessionId = '';
				currentSession = null;
				hasUnsavedChanges = false;
				recordSavedBaseline(null);
				lastSavedTime = null;
				tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });
			}
		} catch (err) {
			// A thrown HTTP 404 proves the session is gone; anything else (backend
			// unreachable/restarting) must leave the tab's link intact so loadSessions'
			// retry can still bind currentSession once the backend answers.
			if (isSessionGoneError(err)) {
				logger.error('Failed to sync session from tab:', err);
				selectedSessionId = '';
				currentSession = null;
				hasUnsavedChanges = false;
				recordSavedBaseline(null);
				lastSavedTime = null;
				tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });
			} else {
				logger.warn('[Session] Backend unreachable while syncing session, keeping the saved link:', err);
			}
		}
	}

	function recordSavedBaseline(signature: string | null, awaitingFormNormalization = false) {
		savedSessionSignature = signature;
		tabsStore.updateTab(tabId, {
			savedSessionSignature: signature,
			sessionBaselineAwaitingFormNormalization: awaitingFormNormalization
		});
	}

	function startAutoSave() {
		stopAutoSave();
		autoSaveIntervalId = window.setInterval(() => {
			if (currentSession && hasUnsavedChanges && !isQuickSaving) {
				performAutoSave();
			}
		}, autoSaveInterval);
	}

	function stopAutoSave() {
		if (autoSaveIntervalId !== null) {
			clearInterval(autoSaveIntervalId);
			autoSaveIntervalId = null;
		}
	}

	function toggleAutoSave() {
		autoSaveEnabled = !autoSaveEnabled;
		storage.set('autoSaveEnabled', autoSaveEnabled.toString());

		if (autoSaveEnabled) {
			startAutoSave();
		} else {
			stopAutoSave();
		}
	}

	function changeAutoSaveInterval(newInterval: number) {
		autoSaveInterval = newInterval;
		storage.set('autoSaveInterval', newInterval.toString());

		if (autoSaveEnabled) {
			startAutoSave();
		}
	}

	async function performAutoSave() {
		if (!currentSession || !presetId || isQuickSaving) return;

		// Safety check: ensure session belongs to current preset
		if (currentSession.preset_id !== presetId) {
			logger.error('[AutoSave] Session belongs to different preset, aborting auto-save');
			stopAutoSave();
			selectedSessionId = '';
			currentSession = null;
			hasUnsavedChanges = false;
			recordSavedBaseline(null);
			lastSavedTime = null;
			return;
		}

		const savingSession = currentSession;

		try {
			isQuickSaving = true;
			const sessionData = collectCurrentSessionData();

			const response = await api.updateSession(savingSession.id, {
				name: savingSession.name,
				data: sessionData
			});

			if (response.success && response.data) {
				sessions = sessions.map((s) => (s.id === savingSession.id ? response.data! : s));
				currentSession = response.data;
				recordSavedBaseline(JSON.stringify(sessionData));
				hasUnsavedChanges = false;
				lastSavedTime = new Date();
			}
		} catch (err) {
			logger.error('Auto-save failed:', err);
		} finally {
			isQuickSaving = false;
		}
	}

	async function loadSessions() {
		if (!presetId) return;

		if (loadSessionsRetryTimer !== null) {
			clearTimeout(loadSessionsRetryTimer);
			loadSessionsRetryTimer = null;
		}

		try {
			isSessionLoading = true;
			error = null;
			const response = await api.getSessionsForPreset(presetId);
			if (response.success && response.data) {
				sessions = response.data;
				const sessionForTab = response.data.find((session) => session.id === selectedSessionId) ?? null;
				if (sessionForTab) {
					currentSession = sessionForTab;
					lastSavedTime = new Date(sessionForTab.updated_at);
					hasUnsavedChanges = sessionIsDirty(true, savedSessionSignature, currentSessionSignature);
				}
			}
			loadSessionsRetryDelay = 2000;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load sessions';
			logger.error('Failed to load sessions:', err);
			loadSessionsRetryTimer = window.setTimeout(loadSessions, loadSessionsRetryDelay);
			loadSessionsRetryDelay = Math.min(loadSessionsRetryDelay * 2, 30000);
		} finally {
			isSessionLoading = false;
		}
	}

	/**
	 * Apply one mode's session data to this tab — the single code path used to
	 * load both a normal session and a historical version of one (see
	 * handleSessionSelect and handleRestoreVersion). `modeBasedData` is the
	 * mode-keyed payload (a session's `.data`, or a history version's `.data`);
	 * `sessionMeta` is the (always current) Session record used for identity/
	 * metadata. When `markSaved` is true, the just-applied data becomes the new
	 * "no unsaved changes" baseline (a normal load). When false (a historical
	 * restore), the baseline is left pointing at nothing so the tab reads as
	 * having unsaved changes until the user hits Save — restoring a save never
	 * silently becomes "the latest" on its own.
	 */
	async function applySessionModeData(
		sessionId: string,
		modeBasedData: ModeBasedSessionData,
		sessionMeta: Session,
		options: { markSaved: boolean }
	) {
		// If the payload has no data for the current mode, load an empty state
		// for this mode — user can then fill it in and save, accumulating
		// per-mode data on the same session.
		const modeData: SessionData = (currentMode && modeBasedData[currentMode]) || {};

		// Fall back to the mode's default variant, non-fatally, if the saved
		// variant no longer exists (e.g. the preset dropped/renamed a form).
		const restoredVariant = resolveVariant(currentModeVariants, modeData.selectedVariant ?? null);
		const restoredModeState = seedModeStateFromSessionData(modeBasedData, currentMode);
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
			? JSON.stringify(collectTabSessionData(restoredTab || undefined, currentMode, sessionMeta.data, presetVersion))
			: null;

		tabsStore.updateTab(tabId, {
			// Don't override preset - user is already on the correct preset
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

		selectedSessionId = sessionId;
		currentSession = sessionMeta;
		if (options.markSaved) savedSessionSignature = restoredBaseline;
		lastSavedTime = new Date(sessionMeta.updated_at);
		applySessionLayout(modeBasedData, currentMode);
		warnIfPresetVersionDrifted(modeBasedData, currentMode);
		await tick();

		if (options.markSaved) {
			hasUnsavedChanges = false;
		} else {
			// Null out the baseline rather than pointing it at the session's
			// actual latest data: the reactive diff below would otherwise have to
			// exactly re-derive collectCurrentSessionData()'s field defaulting to
			// avoid a phantom-dirty flag. Leaving it null just pauses that diff —
			// the next real save (confirmSaveSession/handleQuickSave/autosave)
			// recomputes it and hasUnsavedChanges resumes tracking normally.
			recordSavedBaseline(null);
			hasUnsavedChanges = true;
		}
	}

	async function handleSessionSelect(sessionId: string) {
		if (!sessionId) return;

		try {
			isSessionLoading = true;
			error = null;
			selectedSessionId = sessionId;

			const response = await api.getSessionById(sessionId);
			if (response.success && response.data) {
				await applySessionModeData(sessionId, response.data.data, response.data, { markSaved: true });
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load session';
			logger.error('Failed to load session:', err);
		} finally {
			isSessionLoading = false;
		}
	}

	// Session history: list this session's past saves.
	async function openSessionHistory(sessionId: string) {
		historySessionId = sessionId;
		historyVersions = [];
		historyError = null;
		isHistoryLoading = true;

		try {
			const response = await api.getSessionVersions(sessionId);
			if (response.success && response.data) {
				historyVersions = response.data;
			}
		} catch (err) {
			historyError = 'Could not load history for this session.';
			logger.error('Failed to load session history:', err);
		} finally {
			isHistoryLoading = false;
		}
	}

	function closeSessionHistory() {
		historySessionId = null;
		historyVersions = [];
		historyError = null;
	}

	// One-click restore: load a past save into this tab through the exact same
	// path a normal session load uses (applySessionModeData above). There is no
	// restore endpoint — the next normal Save is what makes this the latest.
	async function handleRestoreVersion(sessionId: string, versionNumber: number) {
		const sessionMeta = sessions.find((s) => s.id === sessionId);
		if (!sessionMeta) {
			toasts.error('That session is no longer available.');
			return;
		}

		try {
			isRestoringVersion = true;
			const response = await api.getSessionVersion(sessionId, versionNumber);
			if (response.success && response.data) {
				const version = response.data;
				await applySessionModeData(sessionId, version.data, sessionMeta, { markSaved: false });
				closeSessionHistory();
				toasts.info(`Loaded the save from ${timeAgo(version.created_at)} — Save to make it the latest.`);
			}
		} catch (err) {
			logger.error('Failed to restore session version:', err);
			toasts.error('Could not load that save.');
		} finally {
			isRestoringVersion = false;
		}
	}

	/**
	 * Composes the FULL multi-mode save payload: the active mode's fresh live
	 * snapshot, plus every other mode this tab has visited (from
	 * `modeStateByMode`) overlaid onto the session's last-saved baseline — so
	 * saving captures every mode the user configured this session, not just
	 * whichever one happens to be active when they hit Save.
	 */
	function collectCurrentSessionData(): ModeBasedSessionData {
		return collectTabSessionData(currentTabData, currentMode, currentSession?.data || {}, presetVersion);
	}

	async function handleQuickSave() {
		if (!currentSession || !presetId) {
			handleOpenSaveAsModal();
			return;
		}

		// Safety check: ensure session belongs to current preset
		if (currentSession.preset_id !== presetId) {
			error = 'Cannot save: Session belongs to a different preset';
			logger.error('[QuickSave] Session belongs to different preset, aborting');
			selectedSessionId = '';
			currentSession = null;
			hasUnsavedChanges = false;
			recordSavedBaseline(null);
			lastSavedTime = null;
			return;
		}

		const savingSession = currentSession;

		try {
			isQuickSaving = true;
			error = null;

			const sessionData = collectCurrentSessionData();

			const response = await api.updateSession(savingSession.id, {
				name: savingSession.name,
				data: sessionData
			});

			if (response.success && response.data) {
				sessions = sessions.map((s) => (s.id === savingSession.id ? response.data! : s));
				currentSession = response.data;
				recordSavedBaseline(JSON.stringify(sessionData));
				hasUnsavedChanges = false;
				lastSavedTime = new Date();
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save session';
			logger.error('Failed to quick save:', err);
		} finally {
			isQuickSaving = false;
		}
	}

	function handleOpenSaveModal() {
		if (currentSession) {
			sessionName = currentSession.name;
			showSaveModal = true;
		} else {
			handleOpenSaveAsModal();
		}
		nameError = '';
	}

	function handleOpenSaveAsModal() {
		sessionName = '';
		showSaveAsModal = true;
		nameError = '';
	}

	async function confirmSaveSession(isSaveAs: boolean = false) {
		if (!sessionName.trim()) {
			nameError = 'Session name is required';
			return;
		}

		const existingSession = sessions.find(
			(s) => s.name === sessionName.trim() && (isSaveAs || s.id !== selectedSessionId)
		);

		if (existingSession) {
			nameError = 'A session with this name already exists';
			return;
		}

		try {
			isSaving = true;
			error = null;

			const sessionData = collectCurrentSessionData();

			if (!isSaveAs && selectedSessionId) {
				const response = await api.updateSession(selectedSessionId, {
					name: sessionName.trim(),
					data: sessionData
				});

				if (response.success && response.data) {
					sessions = sessions.map((s) =>
						s.id === selectedSessionId ? response.data! : s
					);
					currentSession = response.data;
					recordSavedBaseline(JSON.stringify(sessionData));
					hasUnsavedChanges = false;
					lastSavedTime = new Date();
				}
			} else {
				const response = await api.saveSession({
					preset_id: presetId!,
					name: sessionName.trim(),
					data: sessionData
				});

				if (response.success && response.data) {
					sessions = [response.data, ...sessions];
					selectedSessionId = response.data.id;
					currentSession = response.data;
					recordSavedBaseline(JSON.stringify(sessionData));
					hasUnsavedChanges = false;
					lastSavedTime = new Date();

					// Update tab store with new session ID
					tabsStore.updateTab(tabId, { selectedSessionId: response.data.id });
				}
			}

			closeModals();
		} catch (err) {
			nameError = err instanceof Error ? err.message : 'Failed to save session';
			logger.error('Failed to save session:', err);
		} finally {
			isSaving = false;
		}
	}

	async function handleDeleteSession() {
		showDeleteConfirm = true;
	}

	async function confirmDelete() {
		if (!selectedSessionId) return;

		try {
			isSessionLoading = true;

			await api.deleteSession(selectedSessionId);

			sessions = sessions.filter((s) => s.id !== selectedSessionId);
			selectedSessionId = '';
			currentSession = null;
			hasUnsavedChanges = false;
			recordSavedBaseline(null);
			lastSavedTime = null;

			tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });

			showDeleteConfirm = false;
		} catch (err) {
			toasts.error(err instanceof Error ? err.message : 'Failed to delete session');
			logger.error('Failed to delete session:', err);
		} finally {
			isSessionLoading = false;
		}
	}

	function closeModals() {
		showSaveModal = false;
		showSaveAsModal = false;
		showDeleteConfirm = false;
		sessionName = '';
		nameError = '';
		error = null;
	}
</script>

{#if isClient}
	<SessionControl
		compact
		enabled={sessionControlsEnabled}
		{sessions}
		{currentSession}
		{selectedSessionId}
		loading={isSessionLoading}
		saving={isQuickSaving}
		dirty={hasUnsavedChanges}
		{lastSavedTime}
		{autoSaveEnabled}
		{autoSaveInterval}
		{historySessionId}
		{historyVersions}
		historyLoading={isHistoryLoading}
		{historyError}
		restoringVersion={isRestoringVersion}
		onSelect={handleSessionSelect}
		onSave={handleQuickSave}
		onSaveAs={handleOpenSaveAsModal}
		onRename={handleOpenSaveModal}
		onDelete={handleDeleteSession}
		onToggleAutoSave={toggleAutoSave}
		onIntervalChange={changeAutoSaveInterval}
		onOpenHistory={openSessionHistory}
		onCloseHistory={closeSessionHistory}
		onRestoreVersion={handleRestoreVersion}
	/>
{/if}

<!-- Session Modals -->
<SessionSaveModal
	isOpen={showSaveModal}
	mode="rename"
	bind:sessionName
	{nameError}
	{error}
	{isSaving}
	on:close={closeModals}
	on:confirm={() => confirmSaveSession(false)}
/>

<SessionSaveModal
	isOpen={showSaveAsModal}
	mode="save-as"
	bind:sessionName
	{nameError}
	{error}
	{isSaving}
	on:close={closeModals}
	on:confirm={() => confirmSaveSession(true)}
/>

<ConfirmModal
	isOpen={showDeleteConfirm}
	title="Delete Session"
	message={`Are you sure you want to delete "${currentSession?.name}"? This action cannot be undone.`}
	variant="danger"
	busy={isSessionLoading}
	on:confirm={confirmDelete}
	on:cancel={closeModals}
/>
