<script lang="ts">
	import { Button } from '$lib/components/ui';

	let {
		name,
		updating = false,
		canApply = true,
		canReset = true,
		onapply,
		onreset
	}: {
		name: string;
		updating?: boolean;
		canApply?: boolean;
		canReset?: boolean;
		onapply: () => void;
		onreset: () => void;
	} = $props();
</script>

<div class="flex gap-2" data-effect-actions data-mode={updating ? 'update' : 'add'}>
	<Button variant="secondary" size="sm" class="h-8 flex-none" disabled={!canReset} onclick={onreset} attrs={{ 'data-options-reset': '' }}>Reset</Button>
	<Button variant="primary" size="sm" class="h-8 min-w-0 flex-1" disabled={!canApply} onclick={onapply} attrs={{ 'data-options-apply': '' }}>
		<span class="min-w-0 truncate">{updating ? 'Update' : 'Add'} {name}</span>
		<kbd class="rounded border border-accent-contrast/25 px-1 font-mono text-sm leading-4 text-accent-contrast/70" aria-hidden="true">↵</kbd>
	</Button>
</div>
