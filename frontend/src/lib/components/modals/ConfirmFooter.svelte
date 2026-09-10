<script lang="ts">
	import { Button, Kbd } from '$lib/components/ui';

	let {
		cancelLabel = 'Cancel',
		confirmLabel,
		confirmVariant = 'primary',
		busy = false,
		confirmDisabled = false,
		secondaryLabel,
		secondaryIcon,
		secondaryBusy = false,
		secondaryDisabled = false,
		onCancel,
		onConfirm,
		onSecondary
	}: {
		cancelLabel?: string;
		confirmLabel: string;
		confirmVariant?: 'primary' | 'danger';
		busy?: boolean;
		confirmDisabled?: boolean;
		/** An optional extra action rendered between Cancel and Confirm, e.g. "Save to Library". */
		secondaryLabel?: string;
		secondaryIcon?: string;
		secondaryBusy?: boolean;
		secondaryDisabled?: boolean;
		onCancel: () => void;
		onConfirm: () => void;
		onSecondary?: () => void;
	} = $props();

	// One action at a time: while either the confirm or the secondary action
	// is mid-flight, every button in the footer waits.
	let anyBusy = $derived(busy || secondaryBusy);
</script>

<div class="flex items-center justify-end gap-3 px-6 py-4">
	<Button variant="secondary" disabled={anyBusy} onclick={onCancel}>
		<span class="inline-flex items-center gap-2">
			{cancelLabel}
			<Kbd keys="Esc" />
		</span>
	</Button>
	{#if secondaryLabel && onSecondary}
		<Button
			variant="secondary"
			icon={secondaryIcon}
			disabled={anyBusy || secondaryDisabled}
			loading={secondaryBusy}
			onclick={onSecondary}
		>
			{secondaryLabel}
		</Button>
	{/if}
	<Button
		variant={confirmVariant}
		disabled={anyBusy || confirmDisabled}
		loading={busy}
		initialFocus
		onclick={onConfirm}
	>
		<span class="inline-flex items-center gap-2">
			{confirmLabel}
			<Kbd keys="Enter" />
		</span>
	</Button>
</div>
