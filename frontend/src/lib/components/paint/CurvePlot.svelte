<script lang="ts">
	import { curvePath, type CurvePoint } from '$lib/filters/curve';

	export let curves: Array<{ id: string; points: CurvePoint[] }> = [];
	export let label = 'Curves';

	const WIDTH = 160;
	const HEIGHT = 80;
	const IDENTITY: CurvePoint[] = [
		[0, 0],
		[1, 1]
	];

	$: shown = curves.length > 0 ? curves : [{ id: 'master', points: IDENTITY }];
</script>

<svg
	role="img"
	aria-label="{label} plot, read only"
	viewBox="0 0 {WIDTH} {HEIGHT}"
	class="block h-20 w-full rounded border border-line bg-surface-2"
	preserveAspectRatio="none"
>
	<path d="M0 {HEIGHT}L{WIDTH} 0" class="stroke-line-strong" fill="none" stroke-dasharray="3 3" stroke-width="1" vector-effect="non-scaling-stroke" />
	{#each shown as curve (curve.id)}
		<path
			d={curvePath(curve.points, WIDTH, HEIGHT)}
			class="stroke-signal"
			fill="none"
			stroke-width="1.5"
			vector-effect="non-scaling-stroke"
		/>
	{/each}
</svg>
