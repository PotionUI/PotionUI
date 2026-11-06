<script lang="ts">
	import { sessionDrafts } from '$lib/stores/tabDraftSync';
	import { sessionLiveSync } from '$lib/stores/sessionLiveSync';
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import { tabsStore } from '$lib/stores/tabs';
	import type { PresetModeVariant } from '$lib/types/api';
	import { sortVariants } from '$lib/utils/variants';
	import { createSessionController } from '$lib/session/sessionController';
	import { createSessionDialogs } from '$lib/session/sessionDialogs';
	import { loadFieldLabels } from '$lib/session/sessionFieldLabels';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import { activeWorkspaceSaveRequest, settleWorkspaceSaveRequest } from '$lib/stores/workspaceSaveRequest';
	import { activeWorkspaceDirtyQuery, answerWorkspaceDirtyQuery } from '$lib/stores/workspaceDirtyQuery';
	import { setTabSessionDirty, clearTabSessionDirty } from '$lib/stores/tabSessionDirty';
	import { toasts } from '$lib/stores/toast';
	import SessionHost from '$lib/components/session/SessionHost.svelte';

	export let presetId: string | null = null;
	export let currentMode: string | null = null;
	export let tabId: string;
	export let presetVersion: string | undefined = undefined;
	export let availableModes: Array<{ id: string; variants?: PresetModeVariant[] }> = [];

	const controller = createSessionController({
		api,
		tabs: tabsStore,
		storage,
		toasts,
		logger,
		drafts: sessionDrafts,
		live: sessionLiveSync
	});
	const session = controller.state;
	const dialogs = createSessionDialogs(controller);

	let fieldLabels: FieldLabels = {};
	let publishedTabId = '';

	$: currentModeVariants = sortVariants(availableModes.find((mode) => mode.id === currentMode)?.variants);

	$: controller.setContext({
		tabId,
		presetId,
		currentMode,
		presetVersion,
		modeVariants: currentModeVariants
	});

	$: refreshFieldLabels(presetId, currentMode);

	async function refreshFieldLabels(preset: string | null, mode: string | null) {
		const labels = await loadFieldLabels(preset, mode);
		if (preset === presetId && mode === currentMode) fieldLabels = labels;
	}

	$: publishDirty(tabId, !!$session.currentSession && $session.hasUnsavedChanges);

	function publishDirty(id: string, dirty: boolean) {
		if (publishedTabId && publishedTabId !== id) clearTabSessionDirty(publishedTabId);
		publishedTabId = id;
		if (id) setTabSessionDirty(id, dirty);
	}

	$: if ($activeWorkspaceSaveRequest && $activeWorkspaceSaveRequest.tabId === tabId) {
		void handleWorkspaceSaveRequest($activeWorkspaceSaveRequest.id);
	}

	async function handleWorkspaceSaveRequest(requestId: number) {
		if (!$session.sessionControlsEnabled || !$session.currentSession || !$session.hasUnsavedChanges) {
			settleWorkspaceSaveRequest(requestId, true);
			return;
		}
		await dialogs.quickSave();
		settleWorkspaceSaveRequest(requestId, !$session.error);
	}

	$: if ($activeWorkspaceDirtyQuery && $activeWorkspaceDirtyQuery.tabId === tabId) {
		answerWorkspaceDirtyQuery($activeWorkspaceDirtyQuery.id, $session.hasUnsavedChanges);
	}

	onMount(() => {
		controller.start();
	});

	onDestroy(() => {
		controller.destroy();
		if (publishedTabId) clearTabSessionDirty(publishedTabId);
	});
</script>

<slot session={$session} actions={dialogs.actions} {fieldLabels} />

<SessionHost {controller} {dialogs} />
