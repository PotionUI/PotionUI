<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		dirtyCount = 0,
		dirtyLabel,
		children
	}: {
		dirtyCount?: number;
		dirtyLabel?: string;
		children?: Snippet;
	} = $props();

	const label = $derived(dirtyLabel ?? `${dirtyCount} unsaved change${dirtyCount === 1 ? '' : 's'}`);
</script>

<div
	class="flex-shrink-0 border-t border-line bg-surface-1 px-4 sm:px-5 py-2.5 flex items-center justify-end gap-2"
>
	{#if dirtyCount > 0}
		<span class="mr-auto flex items-center gap-1.5 font-mono text-xs text-warning">
			<span class="w-1.5 h-1.5 rounded-full bg-warning"></span>
			{label}
		</span>
	{/if}
	{@render children?.()}
</div>
