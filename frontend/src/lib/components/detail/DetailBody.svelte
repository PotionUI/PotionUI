<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		fullWidth = false,
		children
	}: {
		fullWidth?: boolean;
		children?: Snippet;
	} = $props();
</script>

<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2 p-4 sm:p-5">
	{#if fullWidth}
		{@render children?.()}
	{:else}
		<div class="max-w-2xl space-y-5">
			{@render children?.()}
		</div>
	{/if}
</div>
