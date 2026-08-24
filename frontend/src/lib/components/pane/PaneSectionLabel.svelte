<script lang="ts">
	import type { Snippet } from 'svelte';

	let { label, actions }: { label: string; actions?: Snippet } = $props();
</script>

<div class="flex items-center justify-between px-3 h-6">
	<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{label}</span>
	{@render actions?.()}
</div>
