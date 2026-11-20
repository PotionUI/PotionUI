<script lang="ts">
	import CurvePlot from '$lib/components/paint/CurvePlot.svelte';
	import { adjustStepFor } from '$lib/tool-tabs/image/adjustSections';
	import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
	import { CURVE_POINTS, curveMaster, type AdjustParams } from '$lib/tool-tabs/image/steps';
	import type { ImageToolHost } from '$lib/tool-tabs/image/toolHost';

	let { host }: { host: unknown; phone?: boolean; tool?: string } = $props();

	let image = $derived(host as ImageToolHost);
	let stack = $state.raw<EditStackState<unknown>>(createStack());

	$effect(() => image.stack.subscribe((next) => (stack = next)));

	let values = $derived.by(() => {
		const found = adjustStepFor(stack, 'curves');
		const stored = found && !found.hidden ? (found.params as AdjustParams).values : {};
		return Object.fromEntries(CURVE_POINTS.map((point) => [point.id, stored[point.id] ?? 0]));
	});
</script>

<CurvePlot curves={[{ id: 'master', points: curveMaster(values) }]} label="Curves" />
