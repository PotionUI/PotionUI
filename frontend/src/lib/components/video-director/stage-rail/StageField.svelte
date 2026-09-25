<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		label,
		children,
		class: className = ''
	}: {
		label: string;
		children?: Snippet;
		class?: string;
	} = $props();
</script>

<div class="flex flex-col gap-1 {className}">
	<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">{label}</span>
	{@render children?.()}
</div>
