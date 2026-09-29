<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Badge } from '$lib/components/ui';

	let {
		count,
		description,
		variant = 'neutral',
		dot = false,
		children
	}: {
		count: number;
		description: string;
		variant?: 'neutral' | 'warning';
		dot?: boolean;
		children: Snippet;
	} = $props();

	let open = $state(false);
</script>

<div class="space-y-1.5">
	<button
		type="button"
		class="flex items-center gap-2 text-sm text-fg-muted hover:underline"
		onclick={() => (open = !open)}
		aria-expanded={open}
	>
		<Badge {variant} {dot}>
			<span class="font-mono tabular-nums">{count}</span>
		</Badge>
		<span>{description}</span>
		<span class="font-mono text-xs uppercase tracking-[0.05em] text-fg-subtle">{open ? 'Hide' : 'Show'}</span>
	</button>
	{#if open}
		<ul class="max-h-48 overflow-y-auto space-y-1.5 text-sm text-fg-subtle leading-relaxed">
			{@render children()}
		</ul>
	{/if}
</div>
