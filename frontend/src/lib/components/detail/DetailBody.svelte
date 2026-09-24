<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children }: { children?: Snippet } = $props();
</script>

<div class="flex-1 min-h-0 overflow-y-auto bg-surface-2 p-4 sm:p-5">
	{@render children?.()}
</div>
