<script lang="ts">
	let {
		title,
		count,
		unit,
		unitOne,
		total,
		filterCount = 0,
		onOpenFilters,
		showUnit = true,
		narrow = false
	}: {
		title: string;
		count?: number;
		unit?: string;
		unitOne?: string;
		total?: number | null;
		filterCount?: number;
		onOpenFilters?: () => void;
		showUnit?: boolean;
		narrow?: boolean;
	} = $props();

	const format = (value: number) => value.toLocaleString('en-US');
	const unitLabel = $derived(count === 1 && unitOne ? unitOne : unit);
	const filtered = $derived(filterCount > 0 && count !== undefined);
	const filtersLabel = $derived(`${filterCount} ${filterCount === 1 ? 'filter' : 'filters'}`);
</script>

<div
	class="flex min-w-0 flex-col justify-center gap-0.5 {narrow ? 'flex-1' : 'w-[152px] flex-none'}"
	data-header-title
>
	<h1 class="truncate text-lg font-semibold leading-6 text-fg">{title}</h1>
	{#if count !== undefined}
		<div class="flex min-w-0 items-center gap-1.5 whitespace-nowrap text-sm leading-4 text-fg-muted">
			{#if filtered}
				<span class="flex-none">
					<span class="font-mono tabular-nums">{format(count)}</span>{#if total != null && total >= count}{' '}of{' '}<span class="font-mono tabular-nums">{format(total)}</span>{/if}
				</span>
				<span class="flex-none text-fg-disabled" aria-hidden="true">·</span>
				{#if onOpenFilters}
					<button
						type="button"
						class="flex-none rounded text-signal hover:underline hover:underline-offset-2"
						onclick={onOpenFilters}
					>
						{filtersLabel}
					</button>
				{:else}
					<span class="flex-none text-signal">{filtersLabel}</span>
				{/if}
			{:else}
				<span class="truncate">
					<span class="font-mono tabular-nums">{format(count)}</span>{#if showUnit && unitLabel}{' '}{unitLabel}{/if}
				</span>
			{/if}
		</div>
	{/if}
</div>
