<script lang="ts">
	/**
	 * Ranked horizontal bar chart. `colorBy: 'single'` uses one hue for every
	 * bar (nominal categories, e.g. presets) — never a darker-where-bigger
	 * ramp. `colorBy: 'group'` colors by group via colorForKey and shows a
	 * legend, since identity must never be color-alone with 2+ hues in play.
	 */
	import { colorForKey } from './chart-colors';

	interface RankBarDatum {
		key: string;
		label: string;
		count: number;
		group?: string | null;
	}

	let {
		data,
		colorBy = 'single',
		valueFormat = (n: number) => `${n}`,
		class: className = ''
	}: {
		data: RankBarDatum[];
		colorBy?: 'single' | 'group';
		valueFormat?: (n: number) => string;
		class?: string;
	} = $props();

	let maxCount = $derived(Math.max(1, ...data.map((d) => d.count)));

	function widthPct(count: number): number {
		return (count / maxCount) * 100;
	}

	function barColor(d: RankBarDatum): string {
		if (colorBy === 'group' && d.group) return colorForKey(d.group);
		return 'rgb(var(--viz-1))';
	}

	let legendGroups = $derived(
		colorBy === 'group'
			? Array.from(new Set(data.map((d) => d.group).filter((g): g is string => !!g)))
			: []
	);
</script>

<div class={className}>
	{#if colorBy === 'group' && legendGroups.length}
		<div class="mb-3 flex flex-wrap gap-x-3 gap-y-1">
			{#each legendGroups as group (group)}
				<div class="flex items-center gap-1.5 text-xs text-fg-muted">
					<span
						class="inline-block h-2 w-2 rounded-full"
						style="background: {colorForKey(group)};"
					></span>
					{group}
				</div>
			{/each}
		</div>
	{/if}

	<div class="flex flex-col gap-0.5">
		{#each data as d (d.key)}
			<div class="flex items-center gap-2">
				<div class="w-28 shrink-0 truncate text-xs text-fg-muted" title={d.label}>{d.label}</div>
				<div class="relative h-5 flex-1 min-w-0">
					<div
						class="absolute inset-y-0 left-0 rounded-r"
						style="width: {widthPct(d.count)}%; background: {barColor(d)};"
					></div>
				</div>
				<div class="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-fg">
					{valueFormat(d.count)}
				</div>
			</div>
		{/each}
	</div>
</div>
