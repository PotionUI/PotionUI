<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen: boolean = false;
	export let title: string = 'Confirm';
	export let message: string = '';
	export let confirmLabel: string = 'Confirm';
	export let cancelLabel: string | undefined = undefined;
	/** "danger" red, "warning" amber, "info" blue, "success" green. */
	export let variant: 'danger' | 'warning' | 'info' | 'success' = 'warning';
	/** Disables both actions and shows a spinner on Confirm while an async confirm handler is in flight. */
	export let busy: boolean = false;

	const dispatch = createEventDispatcher<{ confirm: void; cancel: void }>();

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
</script>

<ConfirmDialog
	{isOpen}
	size="md"
	compact
	hideCloseButton
	{busy}
	dialogRole="alertdialog"
	labelledBy={titleId}
	{confirmLabel}
	{cancelLabel}
	confirmVariant={style.confirmVariant}
	onConfirm={() => dispatch('confirm')}
	onCancel={() => dispatch('cancel')}
>
	<div class="p-7">
		<div class="flex items-start gap-4">
			<div class="w-11 h-11 {style.iconBg} rounded-full flex items-center justify-center flex-shrink-0">
				<Icon name={style.icon} className="w-5 h-5 {style.iconColor}" strokeWidth={1.5} />
			</div>
			<div class="min-w-0 pt-0.5">
				<h3 id={titleId} class="text-base font-semibold text-fg mb-1.5 break-words">{title}</h3>
				<p class="text-sm leading-relaxed text-fg-muted whitespace-pre-line break-words">{message}</p>
			</div>
		</div>
	</div>
</ConfirmDialog>
