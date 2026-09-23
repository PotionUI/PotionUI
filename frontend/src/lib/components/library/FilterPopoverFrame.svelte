<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui';

	let {
		label = 'Filters',
		width = 'w-[30rem]',
		onClearAll,
		onClose,
		children
	}: {
		label?: string;
		width?: string;
		onClearAll: () => void;
		onClose: () => void;
		children: Snippet;
	} = $props();
</script>

<div class="{width} max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating" role="dialog" aria-label={label}>
	<div class="mb-3 flex items-center">
		<strong class="text-sm font-semibold text-fg">Filters</strong>
		<button type="button" class="ml-auto text-xs text-fg-subtle hover:text-fg" onclick={onClearAll}>Clear all</button>
	</div>

	<div class="grid grid-cols-2 gap-x-4 gap-y-3">
		{@render children()}
	</div>

	<div class="mt-3.5 flex items-center justify-between border-t border-line pt-3">
		<span class="text-xs text-fg-subtle">Filters apply as you change them.</span>
		<Button size="xs" variant="secondary" onclick={onClose}>Done</Button>
	</div>
</div>
