<script lang="ts">
	import NumberValuesInput from './NumberValuesInput.svelte';
	import { numberAxisValues } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		candidate,
		axis,
		onChange
	}: {
		candidate: AxisCandidate;
		axis: CompareAxis | null;
		tabId?: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let step = $derived(candidate.step ?? 1);
	const initial = (() => {
		const first = axis?.values[0]?.value;
		if (typeof first === 'number') return first;
		return typeof candidate.currentValue === 'number' ? candidate.currentValue : (candidate.min ?? 0);
	})();
</script>

<NumberValuesInput
	min={candidate.min}
	max={candidate.max}
	{step}
	{initial}
	onValues={(values) => onChange(numberAxisValues(values, step))}
/>
