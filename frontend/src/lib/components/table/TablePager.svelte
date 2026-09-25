<script lang="ts">
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { canGoNext, canGoPrev } from './pager';

	let {
		page,
		pageCount,
		pageSize,
		pageSizeOptions = [10, 25, 50, 100],
		onPageChange,
		onPageSizeChange,
		class: className = ''
	}: {
		page: number;
		pageCount: number;
		pageSize: number;
		pageSizeOptions?: readonly number[];
		onPageChange: (page: number) => void;
		onPageSizeChange: (pageSize: number) => void;
		class?: string;
	} = $props();
</script>

<div
	class="flex flex-shrink-0 flex-col gap-2 border-t border-line bg-surface-1 px-3.5 py-2 sm:flex-row sm:items-center sm:justify-between {className}"
>
	<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-muted">Page {page} of {pageCount}</span>
	<div class="flex items-center gap-2 sm:gap-3">
		<span class="whitespace-nowrap text-xs text-fg-subtle">Rows per page</span>
		<select
			class="input h-7 w-20 flex-none appearance-none py-0 pr-6 text-xs"
			value={pageSize}
			onchange={(event) => onPageSizeChange(Number((event.currentTarget as HTMLSelectElement).value))}
		>
			{#each pageSizeOptions as size (size)}
				<option value={size}>{size}</option>
			{/each}
		</select>
		<Tooltip text="Previous page">
			<IconButton
				icon="chevron-left"
				label="Previous page"
				size="sm"
				variant="secondary"
				disabled={!canGoPrev(page)}
				onclick={() => onPageChange(page - 1)}
			/>
		</Tooltip>
		<Tooltip text="Next page">
			<IconButton
				icon="chevron-right"
				label="Next page"
				size="sm"
				variant="secondary"
				disabled={!canGoNext(page, pageCount)}
				onclick={() => onPageChange(page + 1)}
			/>
		</Tooltip>
	</div>
</div>
