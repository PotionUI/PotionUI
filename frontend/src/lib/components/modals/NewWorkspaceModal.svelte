<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';

	export let isOpen: boolean = false;
	/** Whether a tab OTHER than the one "Save" will act on also has unsaved
	 *  work — see utils/newWorkspace.ts `hasUnsavedWorkOutsideTab`. */
	export let otherTabsAlsoDirty: boolean = false;
	/** Disables all three actions and shows a spinner on Save while it's in flight. */
	export let busy: boolean = false;

	const dispatch = createEventDispatcher<{ save: void; discard: void; cancel: void }>();
	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen) settlementGate.reset();

	const titleId = `new-workspace-title-${Math.random().toString(36).slice(2, 9)}`;

	function saveAction() {
		dispatch('save');
	}

	function discardAction() {
		dispatch('discard');
	}

	function cancelAction() {
		dispatch('cancel');
	}

	function handleCancel() {
		settleIfEligible(settlementGate, !busy, cancelAction);
	}

	function handleDiscard() {
		settleIfEligible(settlementGate, !busy, discardAction);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !busy, saveAction);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	{isOpen}
	title=""
	size="md"
	hideCloseButton
	closeable={!busy}
	handleEscapeKey={false}
	dialogRole="alertdialog"
	labelledBy={titleId}
	on:close={handleCancel}
>
	<div class="p-7">
		<div class="flex items-start gap-4">
			<div class="w-11 h-11 bg-warning/10 rounded-full flex items-center justify-center flex-shrink-0">
				<Icon name="warning" className="w-5 h-5 text-warning" strokeWidth={1.5} />
			</div>
			<div class="min-w-0 pt-0.5">
				<h3 id={titleId} class="text-base font-semibold text-fg mb-1.5 break-words">Unsaved changes</h3>
				<p class="text-sm leading-relaxed text-fg-muted whitespace-pre-line break-words">
					This workspace has unsaved changes. Creating a new workspace closes every open tab.
					{#if otherTabsAlsoDirty}
						Saving keeps only the active tab's changes — other tabs' unsaved edits will be discarded either way.
					{/if}
				</p>
			</div>
		</div>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel={busy ? 'Saving…' : 'Save & create new'}
			busy={busy}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		>
			{#snippet leftActions()}
				<Button variant="danger" disabled={busy} onclick={handleDiscard}>Discard &amp; create new</Button>
			{/snippet}
		</ConfirmFooter>
	</svelte:fragment>
</BaseModal>
