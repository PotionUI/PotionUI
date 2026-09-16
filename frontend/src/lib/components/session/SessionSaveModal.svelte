<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';
	import { Input, Alert } from '$lib/components/ui';

	export let isOpen: boolean = false;
	/** 'rename' updates the current session in place; 'save-as' creates a new one. */
	export let mode: 'rename' | 'save-as' = 'rename';
	export let sessionName: string = '';
	export let nameError: string = '';
	export let error: string | null = null;
	export let isSaving: boolean = false;

	const dispatch = createEventDispatcher<{ close: void; confirm: void }>();
	const settlementGate = createConfirmSettlementGate();

	$: if (isOpen) settlementGate.reset();
	$: title = mode === 'rename' ? 'Rename Session' : 'Save New Session';
	$: confirmLabel = mode === 'rename' ? 'Update' : 'Save';
	$: canConfirm = !!sessionName.trim() && !isSaving;

	function handleCancel() {
		settlementGate.settle(() => dispatch('close'));
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, canConfirm, () => dispatch('confirm'));
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen || isSaving) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} {title} size="sm" closeable={!isSaving} handleEscapeKey={false} on:close={handleCancel}>
	<div class="space-y-4 p-4 sm:p-6">
		<div>
			<label class="mb-1.5 block text-sm font-medium text-fg" for="session-save-name">Name</label>
			<Input
				id="session-save-name"
				type="text"
				bind:value={sessionName}
				placeholder="How you will find it later"
				invalid={!!nameError}
				data-autofocus
			/>
			{#if nameError}
				<p class="mt-1.5 text-xs text-danger">{nameError}</p>
			{/if}
		</div>

		{#if error}
			<Alert variant="danger">{error}</Alert>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			{confirmLabel}
			busy={isSaving}
			confirmDisabled={!sessionName.trim()}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
