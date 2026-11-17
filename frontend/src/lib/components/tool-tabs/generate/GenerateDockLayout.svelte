<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		label,
		strip,
		temp,
		prompt,
		mark,
		showTemp = false,
		progress
	}: {
		label: string;
		strip: Snippet;
		temp?: Snippet;
		prompt: Snippet;
		mark: Snippet;
		showTemp?: boolean;
		progress?: Snippet;
	} = $props();
</script>

<div class="relative grid flex-none grid-cols-[minmax(0,1fr)_auto] border-t border-line bg-surface-1" role="group" aria-label={label} data-floating-prompt="desktop">
	{@render progress?.()}
	<div class="flex min-w-0 flex-col">
		<div class="flex-none overflow-hidden border-b border-line px-3 [container-type:inline-size]" data-dock-row="strip">{@render strip()}</div>
		{#if temp && showTemp}
			<div class="flex min-h-9 min-w-0 flex-none items-center gap-3 px-3 pt-3" data-dock-row="temp">{@render temp()}</div>
		{/if}
		<div class="flex flex-none items-end gap-3 px-3 pb-3 pt-3" data-dock-row="prompt">{@render prompt()}</div>
	</div>
	<div class="flex items-center justify-center border-l border-line px-3" data-dock-mark>{@render mark()}</div>
</div>
