<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from '$lib/components/modals/confirmKeyboard';
	import { Input } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import type { Automation } from '$lib/types/automations';

	export let isOpen = false;

	const dispatch = createEventDispatcher<{ close: void; created: Automation }>();
	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen) settlementGate.reset();

	let name = '';
	let description = '';
	let creating = false;

	function resetAndClose() {
		name = '';
		description = '';
		dispatch('close');
	}

	function handleClose() {
		if (creating) return;
		resetAndClose();
	}

	function attemptCancel() {
		settlementGate.settle(handleClose);
	}

	function attemptConfirm() {
		settleIfEligible(settlementGate, !!name.trim() && !creating, handleCreate);
	}

	function handleModalKeydown(e: KeyboardEvent) {
		if (!isOpen) return;
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') attemptCancel();
		else if (action === 'confirm') attemptConfirm();
		if (suppress) e.preventDefault();
	}

	async function handleCreate() {
		if (!name.trim()) return;
		creating = true;
		try {
			const response = await api.createAutomation({
				name: name.trim(),
				description: description.trim() || undefined,
				graph: { nodes: [], edges: [] }
			});
			if (response.success && response.data) {
				dispatch('created', response.data);
				resetAndClose();
			} else {
				toasts.error(response.error || 'Failed to create automation');
			}
		} catch {
			toasts.error('Failed to create automation');
		} finally {
			creating = false;
			settlementGate.reset();
		}
	}
</script>

<svelte:window on:keydown|capture={handleModalKeydown} />

<BaseModal {isOpen} title="New automation" size="sm" handleEscapeKey={false} on:close={attemptCancel}>
	<div class="p-6 space-y-4">
		<div>
			<label for="automation-name" class="block text-xs font-medium text-fg-muted mb-1.5">Name</label>
			<Input id="automation-name" bind:value={name} placeholder="e.g. Index new LoRAs" />
		</div>
		<div>
			<label for="automation-description" class="block text-xs font-medium text-fg-muted mb-1.5">
				Description <span class="text-fg-subtle">(optional)</span>
			</label>
			<Input id="automation-description" bind:value={description} placeholder="What does this automation do?" />
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Create"
			busy={creating}
			confirmDisabled={!name.trim()}
			onCancel={attemptCancel}
			onConfirm={attemptConfirm}
		/>
	</svelte:fragment>
</BaseModal>
