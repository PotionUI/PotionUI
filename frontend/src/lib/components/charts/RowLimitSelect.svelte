<script lang="ts">
	/**
	 * Row-count selector for a single Stats-page section.
	 * Meant to be passed as `ChartCard`'s `headerExtra` snippet. Persists via
	 * `statsRowLimits` (localStorage — a per-viewer display preference, not
	 * settings-table data) and calls `onchange` so the caller can refetch just
	 * that section.
	 */
	import { statsRowLimits, STATS_ROW_LIMIT_OPTIONS, type StatsSection } from '$lib/stores/statsRowLimits';

	let {
		section,
		value,
		onchange
	}: {
		section: StatsSection;
		value: number;
		onchange: (limit: number) => void;
	} = $props();

	function handleChange(event: Event) {
		const limit = Number((event.target as HTMLSelectElement).value);
		statsRowLimits.setLimit(section, limit);
		onchange(limit);
	}
</script>

<select
	class="input h-6 py-0 pl-1.5 pr-5 text-2xs font-mono tabular-nums"
	{value}
	onchange={handleChange}
	aria-label="Rows to show"
	title="Rows to show"
>
	{#each STATS_ROW_LIMIT_OPTIONS as option (option)}
		<option value={option}>{option}</option>
	{/each}
</select>
