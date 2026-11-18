<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { keybindingsStore } from '$lib/stores/keybindings';
	import type { SessionController } from '$lib/session/sessionController';
	import type { SessionDialogs } from '$lib/session/sessionDialogs';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import SessionSaveModal from './SessionSaveModal.svelte';
	import SessionScriptDialog from './SessionScriptDialog.svelte';

	export let controller: SessionController;
	export let dialogs: SessionDialogs;
	export let scripts = true;
	export let shortcut = true;

	const session = controller.state;
	const dialogState = dialogs.state;

	onMount(() => {
		if (shortcut) keybindingsStore.registerHandler('save_session', dialogs.quickSave);
	});

	onDestroy(() => {
		if (shortcut) keybindingsStore.unregisterHandler('save_session');
	});
</script>

<SessionSaveModal
	isOpen={$dialogState.saveOpen}
	mode="rename"
	bind:sessionName={$dialogState.name}
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={dialogs.close}
	on:confirm={() => dialogs.confirmSave(false)}
/>

<SessionSaveModal
	isOpen={$dialogState.saveAsOpen}
	mode="save-as"
	bind:sessionName={$dialogState.name}
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={dialogs.close}
	on:confirm={() => dialogs.confirmSave(true)}
/>

<ConfirmModal
	isOpen={$dialogState.deleteOpen}
	title="Delete Session"
	message={`Are you sure you want to delete "${$session.currentSession?.name}"? This action cannot be undone.`}
	variant="danger"
	busy={$session.isSessionLoading}
	on:confirm={dialogs.confirmDelete}
	on:cancel={dialogs.close}
/>

<ConfirmModal
	isOpen={$dialogState.reloadOpen}
	title="Reload last snapshot"
	message={`Discard your changes and reload the last snapshot of "${$session.currentSession?.name}"?`}
	confirmLabel="Reload"
	variant="warning"
	busy={$session.isSessionLoading || $session.isQuickSaving || $session.isSaving}
	on:confirm={dialogs.confirmReload}
	on:cancel={dialogs.close}
/>

{#if scripts}
<SessionScriptDialog
	sessionId={$dialogState.scriptSessionId}
	sessionName={$session.sessions?.find((item) => item.id === $dialogState.scriptSessionId)?.name ?? ''}
	onClose={dialogs.close}
/>
{/if}
