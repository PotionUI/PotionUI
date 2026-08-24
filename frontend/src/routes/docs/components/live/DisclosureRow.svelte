<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		expanded,
		onToggle,
		panelClass = 'space-y-3',
		trigger,
		children
	}: {
		expanded: boolean;
		onToggle: () => void;
		panelClass?: string;
		trigger: Snippet;
		children?: Snippet;
	} = $props();
</script>

<div class="border border-line rounded-lg bg-surface-1">
	<button
		type="button"
		class="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left"
		onclick={onToggle}
		aria-expanded={expanded}
	>
		{@render trigger()}
		<svg
			class="w-4 h-4 flex-shrink-0 text-fg-subtle transition-transform {expanded ? 'rotate-180' : ''}"
			fill="none"
			stroke="currentColor"
			viewBox="0 0 24 24"
		>
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
		</svg>
	</button>
	{#if expanded}
		<div class="border-t border-line px-3 py-3 {panelClass}">
			{@render children?.()}
		</div>
	{/if}
</div>
