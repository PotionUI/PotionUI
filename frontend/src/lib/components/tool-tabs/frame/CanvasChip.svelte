<script lang="ts">
	import type { Snippet } from 'svelte';

	let { class: className = '', children }: { class?: string; children: Snippet } = $props();
</script>

<span
	class="inline-flex items-center whitespace-nowrap rounded bg-scrim/60 px-2 py-0.5 font-mono text-sm uppercase tracking-[0.08em] tabular-nums text-scrim-on backdrop-blur-sm {className}"
	data-canvas-chip
>
	{@render children()}
</span>
