<script lang="ts">
	import { Button, Pagination } from '$lib/components/ui';
	import { PAGE_SIZES } from './pickerModel';

	let {
		multiple,
		selectedText,
		atLimit = false,
		rangeText = '',
		paged = true,
		page,
		pages,
		pageSize,
		showPageSize = true,
		confirmLabel,
		confirmDisabled,
		onPage,
		onPageSize,
		onCancel,
		onConfirm
	}: {
		multiple: boolean;
		selectedText: string;
		atLimit?: boolean;
		rangeText?: string;
		paged?: boolean;
		page: number;
		pages: number;
		pageSize: number;
		showPageSize?: boolean;
		confirmLabel: string;
		confirmDisabled: boolean;
		onPage: (page: number) => void;
		onPageSize: (size: number) => void;
		onCancel: () => void;
		onConfirm: () => void;
	} = $props();
</script>

<div class="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-3 md:px-5" data-picker-footer>
	<div class="order-2 flex min-w-[7rem] items-center md:order-1">
		{#if multiple}
			<span
				class="font-mono text-sm tabular-nums {atLimit ? 'text-warning' : 'text-fg-muted'}"
				aria-live="polite"
				data-testid="picker-selected-count"
			>
				{selectedText}
			</span>
		{:else}
			<span class="font-mono text-sm tabular-nums text-fg-muted" data-picker-range>{rangeText}</span>
		{/if}
	</div>

	<div class="order-1 flex w-full items-center justify-center gap-4 overflow-x-auto md:order-2 md:w-auto md:flex-1 md:justify-start">
		{#if paged && showPageSize}
			<label class="hidden items-center gap-2 md:flex">
				<span class="text-sm text-fg-muted">Per page</span>
				<select
					class="input !h-8 !min-h-8 !w-auto !py-0 text-sm"
					aria-label="Items per page"
					value={String(pageSize)}
					onchange={(event) => onPageSize(Number(event.currentTarget.value))}
				>
					{#each PAGE_SIZES as size (size)}
						<option value={String(size)}>{size}</option>
					{/each}
				</select>
			</label>
		{/if}
		{#if paged}
			<Pagination currentPage={page} totalPages={pages} size="sm" phoneEdges={false} onPageChange={onPage} />
		{/if}
	</div>

	<div class="order-3 ml-auto flex items-center gap-2">
		<Button variant="ghost" onclick={onCancel}>Cancel</Button>
		{#if multiple}
			<Button variant="primary" disabled={confirmDisabled} onclick={onConfirm} attrs={{ 'data-picker-confirm': '' }}>{confirmLabel}</Button>
		{/if}
	</div>
</div>
