<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from './BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import { createConfirmSettlementGate, resolveConfirmKeydown } from './confirmKeyboard';

	export let isOpen: boolean = false;
	export let title: string = 'Confirm';
	export let message: string = '';
	/** "danger" red, "warning" amber, "info" blue, "success" green. */
	export let variant: 'danger' | 'warning' | 'info' | 'success' = 'warning';
	/** Disables both actions and shows a spinner on Confirm while an async confirm handler is in flight. */
	export let busy: boolean = false;

	const dispatch = createEventDispatcher<{ confirm: void; cancel: void }>();
	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen) settlementGate.reset();

	const titleId = `confirm-title-${Math.random().toString(36).slice(2, 9)}`;

	const variantStyles = {
		danger: {
			iconBg: 'bg-danger/10',
			iconColor: 'text-danger',
			icon: 'warning',
			confirmVariant: 'danger' as const
		},
		warning: {
			iconBg: 'bg-warning/10',
			iconColor: 'text-warning',
			icon: 'warning',
			confirmVariant: 'primary' as const
		},
		info: {
			iconBg: 'bg-info/10',
			iconColor: 'text-info',
			icon: 'info',
			confirmVariant: 'primary' as const
		},
		success: {
			iconBg: 'bg-success/10',
			iconColor: 'text-success',
			icon: 'check',
			confirmVariant: 'primary' as const
		}
	};

	$: style = variantStyles[variant];

	function confirmAction() {
		dispatch('confirm');
	}

	function cancelAction() {
		dispatch('cancel');
	}

	function handleConfirm() {
		settlementGate.settle(confirmAction);
	}

	function handleCancel() {
		settlementGate.settle(cancelAction);
	}

	function handleKeydown(e: KeyboardEvent) {
		const suppress = resolveConfirmKeydown(e, isOpen && !busy, settlementGate, {
			confirm: confirmAction,
			cancel: cancelAction
		});
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
		<div class="flex items-start gap-4 mb-7">
			<div class="w-11 h-11 {style.iconBg} rounded-full flex items-center justify-center flex-shrink-0">
				<Icon name={style.icon} className="w-5 h-5 {style.iconColor}" strokeWidth={1.5} />
			</div>
			<div class="min-w-0 pt-0.5">
				<h3 id={titleId} class="text-base font-semibold text-fg mb-1.5 break-words">{title}</h3>
				<p class="text-sm leading-relaxed text-fg-muted whitespace-pre-line break-words">{message}</p>
			</div>
		</div>
		<div class="flex items-center justify-end gap-3">
			<Button variant="secondary" disabled={busy} onclick={handleCancel}>
				<span class="inline-flex items-center gap-2">
					Cancel
					<Kbd keys="Esc" />
				</span>
			</Button>
			<Button
				variant={style.confirmVariant}
				disabled={busy}
				loading={busy}
				initialFocus
				onclick={handleConfirm}
			>
				<span class="inline-flex items-center gap-2">
					Confirm
					<Kbd keys="Enter" />
				</span>
			</Button>
		</div>
	</div>
</BaseModal>
