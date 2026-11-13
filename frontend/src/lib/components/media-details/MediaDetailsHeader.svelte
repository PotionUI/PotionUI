<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		title,
		version,
		rating,
		tools,
		meta
	}: {
		title: string;
		version?: Snippet;
		rating?: Snippet;
		tools?: Snippet;
		meta?: Snippet;
	} = $props();
</script>

<div
	class="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-0.5 sm:grid-cols-[minmax(0,1fr)_auto_auto]"
	data-details-header
>
	<div class="col-start-1 row-start-1 min-w-[8rem]">
		<h2 class="truncate text-lg font-semibold text-fg">{title}{@render version?.()}</h2>
	</div>
	<div class="col-start-2 row-start-1 flex items-center">
		{@render rating?.()}
	</div>
	<div class="col-start-2 row-start-2 flex items-center justify-end gap-1 sm:col-start-3 sm:row-start-1">
		{@render tools?.()}
	</div>
	<div class="col-start-1 row-start-2 flex min-w-0 items-center gap-2 text-sm text-fg-muted sm:col-span-3" data-details-meta>
		{@render meta?.()}
	</div>
</div>
