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
	class="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:gap-x-3"
	data-details-header
>
	<div class="min-w-[8rem] max-sm:contents sm:col-start-1 sm:flex sm:flex-col sm:gap-0.5">
		<div class="min-w-[8rem] max-sm:col-start-1 max-sm:row-start-1">
			<h2 class="truncate text-lg font-semibold leading-6 text-fg">{title}{@render version?.()}</h2>
		</div>
		<div
			class="flex min-w-0 items-center gap-2 text-sm leading-4 text-fg-muted max-sm:col-start-1 max-sm:row-start-2"
			data-details-meta
		>
			{@render meta?.()}
		</div>
	</div>
	<div class="flex items-center max-sm:col-start-2 max-sm:row-start-1 sm:col-start-2">
		{@render rating?.()}
	</div>
	<div class="flex items-center justify-end gap-1 max-sm:col-start-2 max-sm:row-start-2 max-sm:-my-2 sm:col-start-3">
		{@render tools?.()}
	</div>
</div>
