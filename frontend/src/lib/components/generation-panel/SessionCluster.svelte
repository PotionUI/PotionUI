<script lang="ts">
	// Session + save readout cells for the generation console bar. The session
	// workflow itself lives in the shared controller (lib/session/sessionController.ts),
	// which this view and session/SessionPill.svelte both drive; what is local
	// here is the chrome: the popover body is the shared SessionPopoverContent,
	// anchored UP since this bar sits at the bottom of the viewport.
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy, tick } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import { keybindingsStore, shortcutLabels } from '$lib/stores/keybindings';
	import type { PresetModeVariant } from '$lib/types/api';
	import { sortVariants } from '$lib/utils/variants';
	import { createSessionController, saveOrPrompt } from '$lib/session/sessionController';
	import { activeWorkspaceSaveRequest, settleWorkspaceSaveRequest } from '$lib/stores/workspaceSaveRequest';
	import { activeWorkspaceDirtyQuery, answerWorkspaceDirtyQuery } from '$lib/stores/workspaceDirtyQuery';
	import { toasts } from '$lib/stores/toast';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import SessionPopoverContent from './SessionPopoverContent.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import SessionSaveModal from '$lib/components/session/SessionSaveModal.svelte';

	export let presetId: string | null = null;
	export let currentMode: string | null = null;
	export let tabId: string;
	export let presetVersion: string | undefined = undefined;
	export let availableModes: Array<{ id: string; variants?: PresetModeVariant[] }> = [];

	const controller = createSessionController({ api, tabs: tabsStore, storage, toasts, logger });
	const session = controller.state;

	$: currentModeVariants = sortVariants(availableModes.find((mode) => mode.id === currentMode)?.variants);

	$: controller.setContext({
		tabId,
		presetId,
		currentMode,
		presetVersion,
		modeVariants: currentModeVariants
	});

	let showSaveModal = false;
	let showSaveAsModal = false;
	let showDeleteConfirm = false;
	let sessionName = '';

	let open = false;
	let root: HTMLDivElement;
	let sessionButtonEl: HTMLButtonElement;
	let popoverEl: HTMLDivElement;
	// `.floating-panel` is `position: fixed` (generation-panel-concept.html's
	// own `positionPopover`, lines 678-683) rather than an absolutely
	// positioned dropdown, because its real ancestor here is
	// `.context-rail`, which clips overflow — an absolute popover would be
	// invisible under it. Positioned imperatively against the trigger's
	// rect on open; a resize closes it, exactly like the mock.
	let popoverStyle = '';

	async function positionPopover() {
		await tick();
		if (!popoverEl || !sessionButtonEl) return;
		const rect = sessionButtonEl.getBoundingClientRect();
		const width = popoverEl.offsetWidth || 330;
		const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.left));
		const bottom = window.innerHeight - rect.top + 8;
		popoverStyle = `left:${left}px; bottom:${bottom}px;`;
	}

	// "New workspace" (TabBar) asks whichever tab owns this mounted instance to
	// run its real save action before wiping. See stores/workspaceSaveRequest.ts.
	$: if ($activeWorkspaceSaveRequest && $activeWorkspaceSaveRequest.tabId === tabId) {
		void handleWorkspaceSaveRequest($activeWorkspaceSaveRequest.id);
	}

	async function handleWorkspaceSaveRequest(requestId: number) {
		if (!$session.sessionControlsEnabled || !$session.currentSession || !$session.hasUnsavedChanges) {
			// Nothing saveable on this tab right now (no session, or already
			// clean) - nothing to do, so this is a success from the caller's
			// point of view.
			settleWorkspaceSaveRequest(requestId, true);
			return;
		}
		await handleQuickSave();
		settleWorkspaceSaveRequest(requestId, !$session.error);
	}

	// "New workspace" also needs to know, at the moment of the click, whether
	// THIS tab (the only one with a live `hasUnsavedChanges`) is currently
	// dirty. A one-shot answer to a one-shot query, unlike the sessionDirty
	// push this replaced: continuously writing a dirty flag onto the shared
	// tabsStore on every `hasUnsavedChanges` transition churned the tabs array
	// during normal typing and intermittently stole focus from the segment
	// editor mid-keystroke (regressed session-tab-switch-preserves-draft.spec.ts).
	// A query answered only when asked has no such steady-state cost.
	$: if ($activeWorkspaceDirtyQuery && $activeWorkspaceDirtyQuery.tabId === tabId) {
		answerWorkspaceDirtyQuery($activeWorkspaceDirtyQuery.id, $session.hasUnsavedChanges);
	}

	$: saveCellText = !$session.sessionControlsEnabled
		? 'Unavailable'
		: !$session.currentSession
			? 'Save as new'
			: $session.isQuickSaving
				? 'Saving…'
				: $session.hasUnsavedChanges
					? $session.autoSaveEnabled
						? `Unsaved · ${$session.autoSaveInterval / 1000}s`
						: 'Unsaved changes'
					: `Saved ${timeAgo($session.lastSavedTime?.toISOString())}`;
	$: saveCellClass = !$session.sessionControlsEnabled
		? 'text-fg-disabled'
		: !$session.currentSession
			? 'text-signal'
			: $session.hasUnsavedChanges || $session.isQuickSaving
				? 'text-warning'
				: 'text-fg-subtle';
	$: saveCellAriaLabel = !$session.sessionControlsEnabled
		? 'Session save unavailable'
		: !$session.currentSession
			? 'Save as a new session'
			: $session.isQuickSaving
				? 'Saving session'
				: $session.hasUnsavedChanges
					? 'Save session'
					: 'Session saved';

	function closePanel() {
		open = false;
		controller.closeHistory();
	}

	function toggleOpen() {
		if (!$session.sessionControlsEnabled) return;
		open = !open;
		if (!open) controller.closeHistory();
		else positionPopover();
	}

	function handleWindowResize() {
		if (open) closePanel();
	}

	function handleSaveCellClick() {
		if (!$session.sessionControlsEnabled) return;
		if (!$session.currentSession) {
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
		controller.start();

		document.addEventListener('mousedown', handleWindowClick);
		window.addEventListener('resize', handleWindowResize);
		// "S" (seeded in keybinding_defaults) reuses the same save-or-prompt
		// flow as the save cell's own click handler. SessionPill registers the
		// same action for its mobile counterpart - the two are mutually
		// exclusive by $isMobile, so only one is ever mounted at a time.
		keybindingsStore.registerHandler('save_session', handleQuickSave);
		return () => {
			document.removeEventListener('mousedown', handleWindowClick);
			window.removeEventListener('resize', handleWindowResize);
		};
	});

	onDestroy(() => {
		controller.destroy();
		keybindingsStore.unregisterHandler('save_session');
	});

	async function handleSessionSelect(sessionId: string) {
		await controller.select(sessionId);
		closePanel();
	}

	async function handleQuickSave() {
		await saveOrPrompt(controller, handleOpenSaveAsModal);
	}

	function handleOpenSaveModal() {
		if ($session.currentSession) {
			sessionName = $session.currentSession.name;
			showSaveModal = true;
			controller.openDialog('save');
		} else {
			handleOpenSaveAsModal();
		}
		controller.clearNameError();
	}

	function handleOpenSaveAsModal() {
		sessionName = '';
		showSaveAsModal = true;
		controller.openDialog('save');
		controller.clearNameError();
	}

	async function confirmSaveSession(isSaveAs: boolean = false) {
		if (await controller.saveAs(sessionName, isSaveAs ? 'save-as' : 'rename')) closeModals();
	}

	function handleDeleteSession() {
		showDeleteConfirm = true;
		controller.openDialog('delete');
	}

	async function confirmDelete() {
		if (await controller.deleteSession()) {
			showDeleteConfirm = false;
			controller.closeDialog('delete');
		}
	}

	function closeModals() {
		showSaveModal = false;
		showSaveAsModal = false;
		showDeleteConfirm = false;
		sessionName = '';
		// The controller must hear about a cancel too: a save or delete still in
		// flight answers for the opening it started from, not for the next one.
		controller.closeDialog('save');
		controller.closeDialog('delete');
		controller.clearFeedback();
	}
</script>

<div class="session-control" bind:this={root}>
	<!-- Ported literally from generation-panel-concept.html's `.session-control`
	     > `.session-button` (lines 186-187, 443-450): the name+chevron line
	     AND the passive status line (dirty-dot + save status text) both live
	     inside this one button, which opens the picker — aria-label stays the
	     constant "Session" (real contract: button[aria-label="Session"] in
	     session-edit-survives-tab-switch.spec.ts / sessionClusterTabSwitchClobber.test.ts). -->
	<button
		type="button"
		class="session-button"
		bind:this={sessionButtonEl}
		disabled={!$session.sessionControlsEnabled}
		aria-label="Session"
		aria-haspopup="dialog"
		aria-expanded={open}
		on:click={toggleOpen}
	>
		<span class="cell-copy">
			<span class="session-name-line">
				<span class="session-name">{$session.currentSession ? $session.currentSession.name : 'None'}</span>
				<span class="session-chevron"><svg class="icon"><use href="#i-chevron" /></svg></span>
			</span>
			<span class="session-state">
				<span class="dirty-dot {$session.hasUnsavedChanges ? '' : 'is-saved'}" aria-hidden="true"></span>
				<span class={saveCellClass}>{saveCellText}</span>
			</span>
		</span>
	</button>

	<!-- The mock's own icon-only `.session-save-button` (32px) — the actual
	     save action. Its aria-label is the dynamic one ("Save session" /
	     "Session saved" / "Save as a new session" / "Session save
	     unavailable"): real contract (session-edit-survives-tab-switch.spec.ts,
	     button[aria-label="Save session"] etc.) matches on the attribute, not
	     on visible text, so an icon-only button satisfies it unchanged. -->
	<Tooltip text={saveCellAriaLabel} kbd={$shortcutLabels['save_session']} position="top" delay={150}>
		<button
			type="button"
			class="session-save-button"
			disabled={!$session.sessionControlsEnabled}
			aria-label={saveCellAriaLabel}
			on:click={handleSaveCellClick}
		>
			<svg class="icon"><use href="#i-save" /></svg>
		</button>
	</Tooltip>

	{#if open}
		<!-- role="menu" (not the mock's role="dialog"): keeps the existing
		     [role="menuitem"] row contract (sessionClusterTabSwitchClobber.test.ts). -->
		<div class="floating-panel" style={popoverStyle} bind:this={popoverEl} role="menu" aria-label="Sessions">
			<SessionPopoverContent
				sessions={$session.sessions}
				currentSession={$session.currentSession}
				selectedSessionId={$session.selectedSessionId}
				loading={$session.isSessionLoading}
				historySessionId={$session.historySessionId}
				historyVersions={$session.historyVersions}
				historyLoading={$session.isHistoryLoading}
				historyError={$session.historyError}
				restoringVersion={$session.isRestoringVersion}
				autoSaveEnabled={$session.autoSaveEnabled}
				autoSaveInterval={$session.autoSaveInterval}
				onSelect={handleSessionSelect}
				onSaveAs={() => { closePanel(); handleOpenSaveAsModal(); }}
				onOpenHistory={controller.openHistory}
				onCloseHistory={controller.closeHistory}
				onRestoreVersion={controller.restoreVersion}
				onToggleAutoSave={controller.toggleAutosave}
				onIntervalChange={controller.setAutosaveInterval}
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
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={closeModals}
	on:confirm={() => confirmSaveSession(false)}
/>

<SessionSaveModal
	isOpen={showSaveAsModal}
	mode="save-as"
	bind:sessionName
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={closeModals}
	on:confirm={() => confirmSaveSession(true)}
/>

<ConfirmModal
	isOpen={showDeleteConfirm}
	title="Delete Session"
	message={`Are you sure you want to delete "${$session.currentSession?.name}"? This action cannot be undone.`}
	variant="danger"
	busy={$session.isSessionLoading}
	on:confirm={confirmDelete}
	on:cancel={closeModals}
/>
