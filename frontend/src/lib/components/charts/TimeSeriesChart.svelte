<script lang="ts">
	/**
	 * Single-series time-series area chart, built on LayerChart.
	 *
	 * There is exactly one series, so no legend is rendered — the card title names it.
	 *
	 * LayerChart 1.x is pinned deliberately: 2.x emits `@layer components` inside its component
	 * `<style>` blocks, which is Tailwind v4 syntax and hard-fails PostCSS on this project's
	 * Tailwind 3.4 (`@layer components is used but no matching @tailwind components directive`).
	 * 1.0.13 still declares `svelte: ^5` support. Its components are Svelte-4 authored, so slots
	 * (`let:data`) are used here rather than snippets.
	 *
	 * LayerChart's own utility classes assume its theme (`primary`, `surface-content`), which this
	 * project's Tailwind config does not define. Every mark and rule is therefore coloured
	 * explicitly from our `--viz-*` tokens, and the `@layerstack/tailwind` preset is not installed
	 * (it would collide with our replaced radius/type scales).
	 *
	 * `height` covers the plot AND the x-axis label band, so the card never grows a nested
	 * scrollbar.
	 */
	import { Area, Axis, Chart, Highlight, Svg, Tooltip } from 'layerchart';
	import { scaleTime } from 'd3-scale';

	let {
		data,
		label,
		valueFormat = (n: number) => `${n}`,
		height = 220,
		class: className = ''
	}: {
		data: { bucket: string; value: number }[];
		label: string;
		valueFormat?: (n: number) => string;
		height?: number;
		class?: string;
	} = $props();

	/** Buckets are 'YYYY-MM-DD' for day, but 'YYYY-Www'/'YYYY-MM' otherwise — only days parse. */
	function parseBucket(bucket: string): Date | null {
		if (!/^\d{4}-\d{2}-\d{2}$/.test(bucket)) return null;
		const parsed = new Date(`${bucket}T00:00:00`);
		return Number.isNaN(parsed.getTime()) ? null : parsed;
	}

	let points = $derived(
		data
			.map((d) => ({ date: parseBucket(d.bucket), value: d.value, bucket: d.bucket }))
			.filter((d): d is { date: Date; value: number; bucket: string } => d.date !== null)
	);

	/** A non-day bucket can't sit on a time scale; say so rather than render a wrong axis. */
	let usable = $derived(points.length > 0 && points.length === data.length);

	const dayFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
</script>

<div class={className}>
	{#if usable}
		<div style="height: {height}px;" role="img" aria-label="{label} over time">
			<Chart
				data={points}
				x="date"
				xScale={scaleTime()}
				y="value"
				yDomain={[0, null]}
				yNice
				padding={{ top: 12, right: 12, bottom: 24, left: 52 }}
				tooltip={{ mode: 'bisect-x' }}
				let:width
			>
				<Svg>
					<!-- Gridlines and axes are solid hairlines, one shade off the surface. -->
					<Axis
						placement="left"
						grid={{ class: 'stroke-viz-grid' }}
						rule={false}
						format={(v: number) => valueFormat(v)}
						tickLabelProps={{ class: 'fill-fg-subtle text-2xs' }}
					/>
					<Axis
						placement="bottom"
						rule={{ class: 'stroke-viz-axis' }}
						ticks={Math.max(2, Math.min(6, Math.floor(width / 90)))}
						format={(v: Date) => dayFormat.format(v)}
						tickLabelProps={{ class: 'fill-fg-subtle text-2xs' }}
					/>
					<Area
						line={{ class: 'stroke-viz-1', 'stroke-width': 2 }}
						fill="rgb(var(--viz-1) / 0.15)"
					/>
					<Highlight points={{ class: 'fill-viz-1' }} lines={{ class: 'stroke-viz-axis' }} />
				</Svg>

				<!-- The tooltip enhances; every value is also reachable from the table view. -->
				<Tooltip.Root let:data={d}>
					<Tooltip.Header>{d.bucket}</Tooltip.Header>
					<Tooltip.List>
						<Tooltip.Item {label} value={valueFormat(d.value)} />
					</Tooltip.List>
				</Tooltip.Root>
			</Chart>
		</div>
	{:else}
		<p class="py-8 text-center text-xs text-fg-subtle">No data in range</p>
	{/if}
</div>
