<script lang="ts">
	import { onMount } from 'svelte';
	import { checkboxAxisValues } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		axis,
		onChange
	}: {
		candidate?: AxisCandidate;
		axis: CompareAxis | null;
		tabId?: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	onMount(() => {
		if (!axis || axis.values.length === 0) onChange(checkboxAxisValues());
	});
</script>

<div class="flex gap-1.5">
	{#each checkboxAxisValues() as entry (entry.label)}
		<span class="rounded border border-signal/40 bg-signal/10 px-2 py-0.5 font-mono text-xs text-signal">{entry.label}</span>
	{/each}
</div>
<p class="mt-2 text-xs text-fg-muted">Every cell runs once with this on and once with it off.</p>
