<script lang="ts">
	import { Button, Kbd } from '$lib/components/ui';

	let {
		cancelLabel = 'Cancel',
		confirmLabel,
		confirmVariant = 'primary',
		busy = false,
		confirmDisabled = false,
		onCancel,
		onConfirm
	}: {
		cancelLabel?: string;
		confirmLabel: string;
		confirmVariant?: 'primary' | 'danger';
		busy?: boolean;
		confirmDisabled?: boolean;
		onCancel: () => void;
		onConfirm: () => void;
	} = $props();
</script>

<div class="flex items-center justify-end gap-3 px-6 py-4">
	<Button variant="secondary" disabled={busy} onclick={onCancel}>
		<span class="inline-flex items-center gap-2">
			{cancelLabel}
			<Kbd keys="Esc" />
		</span>
	</Button>
	<Button
		variant={confirmVariant}
		disabled={busy || confirmDisabled}
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
