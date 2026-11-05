<script lang="ts">
	import { Pagination } from '$lib/components/ui';

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

{#if pageCount > 1}
	<div
		class="relative flex flex-shrink-0 items-center justify-center gap-4 border-t border-line bg-surface-1 px-3.5 py-2 {className}"
	>
		<Pagination currentPage={page} totalPages={pageCount} {onPageChange} />

		<div class="absolute right-3.5 hidden items-center gap-2 md:flex">
			<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Per page</span>
			<select
				class="input w-auto bg-surface-2/50 px-2 py-1 font-mono text-xs tabular-nums"
				value={pageSize}
				onchange={(event) => onPageSizeChange(Number((event.currentTarget as HTMLSelectElement).value))}
			>
				{#each pageSizeOptions as size (size)}
					<option value={size}>{size}</option>
				{/each}
			</select>
		</div>
	</div>
{/if}
