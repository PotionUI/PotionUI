<script lang="ts">
	/**
	 * Wraps a chart in a Card with a title, optional subtitle, and a
	 * table-view toggle: the accessibility twin required for the palette's
	 * sub-3:1 light-mode slots, and useful for anyone who wants exact values.
	 *
	 * The table is generated generically from `tableData` + `tableColumns` so
	 * individual chart components don't need to hand-roll their own <table>.
	 */
	import type { Snippet } from 'svelte';
	import { Card, IconButton } from '$lib/components/ui';

	export interface ChartCardColumn {
		key: string;
		label: string;
		align?: 'left' | 'right';
		format?: (value: unknown) => string;
	}

	let {
		title,
		subtitle,
		tableData,
		tableColumns,
		class: className = '',
		children,
		headerExtra
	}: {
		title: string;
		subtitle?: string;
		/** Any row-shaped data; only the keys named in `tableColumns` are read. */
		tableData: readonly unknown[];
		tableColumns: ChartCardColumn[];
		class?: string;
		/** The chart itself. */
		children: Snippet;
		/** Optional control rendered next to the table-view toggle (e.g. a
		 * row-limit selector). */
		headerExtra?: Snippet;
	} = $props();

	let showTable = $state(false);

	function cell(row: unknown, col: ChartCardColumn): string {
		const value = (row as Record<string, unknown>)[col.key];
		if (col.format) return col.format(value);
		return value === null || value === undefined ? '—' : String(value);
	}
</script>

<Card padding="md" class={className}>
	<div class="flex items-start justify-between gap-2 mb-3">
		<div class="min-w-0">
			<h3 class="text-sm font-semibold text-fg truncate">{title}</h3>
			{#if subtitle}
				<p class="mt-0.5 text-xs text-fg-muted truncate">{subtitle}</p>
			{/if}
		</div>
		<div class="flex items-center gap-1.5 shrink-0">
			{#if headerExtra}
				{@render headerExtra()}
			{/if}
			<IconButton
				icon="grid"
				label={showTable ? 'Show chart view' : 'Show table view'}
				active={showTable}
				size="sm"
				onclick={() => (showTable = !showTable)}
			/>
		</div>
	</div>

	{#if showTable}
		<div class="overflow-x-auto">
			<table class="w-full text-xs">
				<thead>
					<tr class="border-b border-line">
						{#each tableColumns as col (col.key)}
							<th
								class="py-1.5 px-2 font-medium text-fg-subtle {col.align === 'right'
									? 'text-right'
									: 'text-left'}"
							>
								{col.label}
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each tableData as row, i (i)}
						<tr class="border-b border-line/50 last:border-0">
							{#each tableColumns as col (col.key)}
								<td
									class="py-1.5 px-2 text-fg {col.align === 'right'
										? 'text-right font-mono tabular-nums'
										: ''}"
								>
									{cell(row, col)}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		{@render children()}
	{/if}
</Card>
