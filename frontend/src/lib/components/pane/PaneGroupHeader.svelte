<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		icon,
		label,
		count,
		actions
	}: {
		icon?: string;
		label: string;
		count?: number;
		actions?: Snippet;
	} = $props();
</script>

<div
	class="sticky top-0 z-10 flex items-center gap-2 px-3 py-2 bg-surface-1/95 border-b border-line font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle"
>
	{#if icon}
		<span class="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded bg-surface-2 text-fg-muted">
			<Icon name={icon} className="w-3.5 h-3.5" />
		</span>
	{/if}
	<span class="truncate">{label}</span>
	{#if count !== undefined || actions}
		<span class="ml-auto flex-shrink-0 flex items-center gap-2">
			{#if count !== undefined}
				<span class="tabular-nums">{count}</span>
			{/if}
			{@render actions?.()}
		</span>
	{/if}
</div>
