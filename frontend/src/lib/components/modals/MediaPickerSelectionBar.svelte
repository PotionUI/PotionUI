<script lang="ts">
	import { Button } from '$lib/components/ui';

	export let count: number;
	export let onCancel: () => void;
	export let onConfirm: () => void;
	export let confirmLabel: string = 'Use selected';
</script>

<div class="flex items-center justify-between gap-3 px-3 md:px-4 py-3 border-b border-line">
	<span class="text-sm text-fg-muted font-mono tabular-nums" aria-live="polite" data-testid="picker-selected-count">
		{count} selected
	</span>
	{#if count === 0}
		<span class="hidden md:inline text-xs text-fg-subtle">Select at least one file to continue</span>
	{/if}
	<div class="flex items-center gap-2">
		<Button variant="ghost" onclick={onCancel}>Cancel</Button>
		<Button
			variant="primary"
			disabled={count === 0}
			onclick={() => count > 0 && onConfirm()}
		>
			{confirmLabel}
		</Button>
	</div>
</div>
