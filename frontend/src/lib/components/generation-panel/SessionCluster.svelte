<script lang="ts">
	// Session + save readout cells for the generation console bar.
	// State/CRUD logic ported from session/SessionPill.svelte; the popover body
	// is the shared SessionPopoverContent, anchored UP since this bar sits at
	// the bottom of the viewport.
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
	import { collectTabSessionData, sessionIsDirty, shouldHydrateSessionSelection } from '$lib/utils/sessionTabState';
	import { toasts } from '$lib/stores/toast';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Icon from '$lib/components/Icon.svelte';
	import ReadoutCell from './ReadoutCell.svelte';
	import SessionPopoverContent from './SessionPopoverContent.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import SessionSaveModal from '$lib/components/session/SessionSaveModal.svelte';

	export let presetId: string | null = null;
	export let currentMode: string | null = null;
	export let tabId: string;
	export let presetVersion: string | undefined = undefined;
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

	let historySessionId: string | null = null;
	let historyVersions: SessionVersionSummary[] = [];
	let isHistoryLoading = false;
	let historyError: string | null = null;
	let isRestoringVersion = false;

	let showSaveModal = false;
	let showSaveAsModal = false;
	let showDeleteConfirm = false;
	let sessionName = '';
	let nameError = '';

	let autoSaveEnabled = false;
	let autoSaveInterval = 10000;
	let autoSaveIntervalId: number | null = null;

	let sessionControlsEnabled = false;

	let open = false;
	let root: HTMLDivElement;

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

	$: sessionControlsEnabled = !!(presetId && currentMode);
	$: currentTabData = $tabsStore.tabs.find((t) => t.id === tabId);

	let adoptedTabId: string | null = null;

	$: if (presetId && currentMode) {
		loadSessions();
	}

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

	$: if (!isSessionLoading && tabId === adoptedTabId && currentTabData?.selectedSessionId) {
		if (shouldHydrateSessionSelection(isClient, selectedSessionId, currentTabData.selectedSessionId)) {
			syncSessionFromTab(currentTabData.selectedSessionId);
		}
	} else if (!isSessionLoading && tabId === adoptedTabId && currentTabData && !currentTabData.selectedSessionId && selectedSessionId) {
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

	$: if (currentTabData && currentTabData.savedSessionSignature !== undefined && currentTabData.savedSessionSignature !== savedSessionSignature) {
		savedSessionSignature = currentTabData.savedSessionSignature;
	}

	$: saveCellText = !sessionControlsEnabled
		? 'Unavailable'
		: !currentSession
			? 'Save as new'
			: isQuickSaving
				? 'Saving…'
				: hasUnsavedChanges
					? autoSaveEnabled
						? `Unsaved · ${autoSaveInterval / 1000}s`
						: 'Unsaved changes'
					: `Saved ${timeAgo(lastSavedTime?.toISOString())}`;
	$: saveCellClass = !sessionControlsEnabled
		? 'text-fg-disabled'
		: !currentSession
			? 'text-signal'
			: hasUnsavedChanges || isQuickSaving
				? 'text-warning'
				: 'text-fg-subtle';
	$: saveCellAriaLabel = !sessionControlsEnabled
		? 'Session save unavailable'
		: !currentSession
			? 'Save as a new session'
			: isQuickSaving
				? 'Saving session'
				: hasUnsavedChanges
					? 'Save session'
					: 'Session saved';

	function closePanel() {
		open = false;
		closeSessionHistory();
	}

	function toggleOpen() {
		if (!sessionControlsEnabled) return;
		open = !open;
		if (!open) closeSessionHistory();
	}

	function handleSaveCellClick() {
		if (!sessionControlsEnabled) return;
		if (!currentSession) {
			handleOpenSaveAsModal();
		} else {
			handleQuickSave();
		}
	}

	function handleWindowClick(event: MouseEvent) {
		if (!open || !root) return;
		const target = event.target as Node;
		if (!target.isConnected) return;
		if (!root.contains(target)) closePanel();
	}

	onMount(() => {
		isClient = true;

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

		document.addEventListener('mousedown', handleWindowClick);
		return () => document.removeEventListener('mousedown', handleWindowClick);
	});

	onDestroy(() => {
		stopAutoSave();
	});

	async function syncSessionFromTab(sessionId: string) {
		if (!sessionId || selectedSessionId === sessionId) return;

		try {
			selectedSessionId = sessionId;
			const response = await api.getSessionById(sessionId);
			if (response.success && response.data) {
				if (response.data.preset_id !== presetId) {
					logger.warn('[Session] Session belongs to different preset, clearing it');
					selectedSessionId = '';
					currentSession = null;
					hasUnsavedChanges = false;
					recordSavedBaseline(null);
					lastSavedTime = null;
					tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });
					return;
				}

				await applySessionModeData(sessionId, response.data.data, response.data, { markSaved: true });
			}
		} catch (err) {
			logger.error('Failed to sync session from tab:', err);
			selectedSessionId = '';
			currentSession = null;
			hasUnsavedChanges = false;
			recordSavedBaseline(null);
			lastSavedTime = null;
			tabsStore.updateTab(tabId, { selectedSessionId: null, savedSessionSignature: null });
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
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load sessions';
			logger.error('Failed to load sessions:', err);
		} finally {
			isSessionLoading = false;
		}
	}

	async function applySessionModeData(
		sessionId: string,
		modeBasedData: ModeBasedSessionData,
		sessionMeta: Session,
		options: { markSaved: boolean }
	) {
		const modeData: SessionData = (currentMode && modeBasedData[currentMode]) || {};

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
		const restoredBaseline = options.markSaved
			? JSON.stringify(collectTabSessionData(restoredTab || undefined, currentMode, sessionMeta.data, presetVersion))
			: null;

		tabsStore.updateTab(tabId, {
			selectedSessionId: sessionId,
			selectedVariant: restoredVariant,
			...restoredPatch,
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
		closePanel();
	}

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

	function collectCurrentSessionData(): ModeBasedSessionData {
		return collectTabSessionData(currentTabData, currentMode, currentSession?.data || {}, presetVersion);
	}

	async function handleQuickSave() {
		if (!currentSession || !presetId) {
			handleOpenSaveAsModal();
			return;
		}

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

<div class="relative flex items-stretch" bind:this={root}>
	<ReadoutCell label="session" mono={false} clickable disabled={!sessionControlsEnabled} onclick={toggleOpen} ariaLabel="Session">
		{#if currentSession}
			<span class="max-w-[170px] truncate font-semibold text-fg">{currentSession.name}</span>
			<span
				class="h-[5px] w-[5px] flex-shrink-0 rounded-full {hasUnsavedChanges ? 'bg-warning-solid' : 'bg-success-solid'}"
				aria-hidden="true"
			></span>
		{:else}
			<span class="font-medium text-fg-muted">None</span>
		{/if}
		<Icon name="chevron-down" className="h-3 w-3 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-0' : 'rotate-180'}" />
	</ReadoutCell>

	<span class="h-[26px] w-px flex-shrink-0 bg-line" aria-hidden="true"></span>

	<ReadoutCell label="save" mono={false} clickable disabled={!sessionControlsEnabled} onclick={handleSaveCellClick} ariaLabel={saveCellAriaLabel}>
		<span class="font-semibold {saveCellClass}">{saveCellText}</span>
	</ReadoutCell>

	{#if open}
		<div class="absolute bottom-full right-0 z-50 mb-1 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-floating" role="menu">
			<SessionPopoverContent
				{sessions}
				{currentSession}
				{selectedSessionId}
				loading={isSessionLoading}
				{historySessionId}
				{historyVersions}
				historyLoading={isHistoryLoading}
				{historyError}
				restoringVersion={isRestoringVersion}
				{autoSaveEnabled}
				{autoSaveInterval}
				onSelect={handleSessionSelect}
				onSaveAs={() => { closePanel(); handleOpenSaveAsModal(); }}
				onOpenHistory={openSessionHistory}
				onCloseHistory={closeSessionHistory}
				onRestoreVersion={handleRestoreVersion}
				onToggleAutoSave={toggleAutoSave}
				onIntervalChange={changeAutoSaveInterval}
				onRename={() => { closePanel(); handleOpenSaveModal(); }}
				onDelete={() => { closePanel(); handleDeleteSession(); }}
			/>
		</div>
	{/if}
</div>

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
