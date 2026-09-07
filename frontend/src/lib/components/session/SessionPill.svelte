<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import { keybindingsStore } from '$lib/stores/keybindings';
	import type { PresetModeVariant } from '$lib/types/api';
	import { sortVariants } from '$lib/utils/variants';
	import { createSessionController, saveOrPrompt } from '$lib/session/sessionController';
	import { toasts } from '$lib/stores/toast';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import SessionSaveModal from '$lib/components/session/SessionSaveModal.svelte';

	// The session half of the old PresetSessionBar, re-homed as the
	// compact pill in the tabs row (top-right). Preset selection/mode/variant
	// state stays in PresetHeader - this component only knows presetId,
	// currentMode and tabId, all sourced from the active tab. The session
	// workflow itself lives in the shared controller
	// (lib/session/sessionController.ts), driven identically by the console
	// bar's generation-panel/SessionCluster.svelte.
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

	const controller = createSessionController({ api, tabs: tabsStore, storage, toasts, logger });
	const session = controller.state;

	let isClient = false;

	// Modal states
	let showSaveModal = false;
	let showSaveAsModal = false;
	let showDeleteConfirm = false;
	let sessionName = '';

	$: currentModeVariants = sortVariants(availableModes.find((mode) => mode.id === currentMode)?.variants);

	$: controller.setContext({
		tabId,
		presetId,
		currentMode,
		presetVersion,
		modeVariants: currentModeVariants
	});

	onMount(() => {
		isClient = true;
		controller.start();
		// "S" (seeded in keybinding_defaults) reuses the same save-or-prompt
		// flow as the pill's own Save control. SessionCluster registers the
		// same action for its desktop counterpart - the two are mutually
		// exclusive by $isMobile, so only one is ever mounted at a time.
		keybindingsStore.registerHandler('save_session', handleQuickSave);
	});

	onDestroy(() => {
		controller.destroy();
		keybindingsStore.unregisterHandler('save_session');
	});

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

{#if isClient}
	<SessionControl
		compact
		enabled={$session.sessionControlsEnabled}
		sessions={$session.sessions}
		currentSession={$session.currentSession}
		selectedSessionId={$session.selectedSessionId}
		loading={$session.isSessionLoading}
		saving={$session.isQuickSaving}
		dirty={$session.hasUnsavedChanges}
		lastSavedTime={$session.lastSavedTime}
		autoSaveEnabled={$session.autoSaveEnabled}
		autoSaveInterval={$session.autoSaveInterval}
		historySessionId={$session.historySessionId}
		historyVersions={$session.historyVersions}
		historyLoading={$session.isHistoryLoading}
		historyError={$session.historyError}
		restoringVersion={$session.isRestoringVersion}
		onSelect={controller.select}
		onSave={handleQuickSave}
		onSaveAs={handleOpenSaveAsModal}
		onRename={handleOpenSaveModal}
		onDelete={handleDeleteSession}
		onToggleAutoSave={controller.toggleAutosave}
		onIntervalChange={controller.setAutosaveInterval}
		onOpenHistory={controller.openHistory}
		onCloseHistory={controller.closeHistory}
		onRestoreVersion={controller.restoreVersion}
	/>
{/if}

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
