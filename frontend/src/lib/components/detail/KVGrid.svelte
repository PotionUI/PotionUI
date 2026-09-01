<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children }: { children?: Snippet } = $props();
</script>

<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
	{@render children?.()}
</div>
