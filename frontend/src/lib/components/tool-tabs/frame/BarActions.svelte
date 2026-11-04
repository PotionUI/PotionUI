<script lang="ts">
	import { Button } from '$lib/components/ui';

	let {
		applyLabel = 'Apply',
		canApply = true,
		canReset = true,
		hint = true,
		onapply,
		onreset
	}: {
		applyLabel?: string;
		canApply?: boolean;
		canReset?: boolean;
		hint?: boolean;
		onapply: () => void;
		onreset: () => void;
	} = $props();
</script>

<Button variant="secondary" size="sm" class="h-7" disabled={!canReset} onclick={onreset} attrs={{ 'data-options-reset': '' }}>Reset</Button>
<Button variant="primary" size="sm" class="h-7" disabled={!canApply} onclick={onapply} attrs={{ 'data-options-apply': '' }}>
	{applyLabel}
	{#if hint}
		<kbd class="ml-0.5 rounded border border-accent-contrast/25 px-1 font-mono text-sm leading-4 text-accent-contrast/70" aria-hidden="true">↵</kbd>
	{/if}
</Button>
