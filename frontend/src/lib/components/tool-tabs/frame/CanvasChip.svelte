<script lang="ts">
	import type { Snippet } from 'svelte';

	let { caps = true, class: className = '', children }: { caps?: boolean; class?: string; children: Snippet } = $props();
</script>

<span
	class="inline-flex items-center whitespace-nowrap rounded bg-scrim/60 px-2 py-0.5 font-mono text-sm tabular-nums text-scrim-on backdrop-blur-sm {caps
		? 'uppercase tracking-[0.08em]'
		: ''} {className}"
	data-canvas-chip
>
	{@render children()}
</span>
