<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';

	type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

	export let isOpen: boolean = true;
	export let title: string;
	export let subtitle: string = '';
	export let size: ModalSize = 'md';
	export let confirmLabel: string = 'Confirm';
	export let cancelLabel: string | undefined = undefined;
	export let confirmVariant: 'primary' | 'danger' = 'primary';
	export let busy: boolean = false;
	export let confirmDisabled: boolean = false;
	export let summary: string | undefined = undefined;
	export let hideCancel: boolean = false;
	export let onConfirm: () => void;
	export let onCancel: () => void;
	export let mountBody: (el: HTMLElement) => void | (() => void);

	const settlementGate = createConfirmSettlementGate();

	$: if (isOpen) settlementGate.reset();

	function handleCancel() {
		settlementGate.settle(() => onCancel());
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !busy && !confirmDisabled, () => onConfirm());
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen || busy) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}

	function bindBody(node: HTMLElement) {
		const cleanup = mountBody?.(node);
		return {
			destroy() {
				if (typeof cleanup === 'function') cleanup();
			}
		};
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	{isOpen}
	{title}
	{subtitle}
	{size}
	closeable={!busy}
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<div class="space-y-4 p-4 sm:p-6 min-w-0" use:bindBody></div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			{cancelLabel}
			{confirmLabel}
			{confirmVariant}
			{busy}
			{confirmDisabled}
			{summary}
			{hideCancel}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
