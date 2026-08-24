<script lang="ts">
	/**
	 * Vertical histogram, one hue. Optional markers (e.g. p50/p95) render as
	 * solid signal-colored lines with a label, positioned at the bucket whose
	 * label matches `at`.
	 */
	let {
		data,
		markers = [],
		height = 200,
		valueFormat = (n: number) => `${n}`,
		class: className = ''
	}: {
		data: { label: string; count: number }[];
		markers?: { label: string; at: string }[];
		height?: number;
		valueFormat?: (n: number) => string;
		class?: string;
	} = $props();

	const AXIS_BAND = 24;
	const PAD = { top: 20, right: 8, bottom: AXIS_BAND, left: 8 };

	let containerWidth = $state(0);
	let hoverIndex = $state<number | null>(null);

	let innerWidth = $derived(Math.max(containerWidth - PAD.left - PAD.right, 1));
	let innerHeight = $derived(Math.max(height - PAD.top - PAD.bottom, 1));

	let maxCount = $derived(Math.max(1, ...data.map((d) => d.count)));
	let n = $derived(data.length || 1);
	let bandWidth = $derived(innerWidth / n);
	const GAP = 2;

	function barX(i: number): number {
		return PAD.left + i * bandWidth;
	}
	function barHeight(count: number): number {
		return (count / maxCount) * innerHeight;
	}

	/**
	 * A bar rounded at its data end only. `rx` on a <rect> rounds all four corners, which lifts
	 * the bar off the baseline; the baseline end must stay square.
	 */
	function barPath(i: number, count: number): string {
		const x = barX(i) + GAP / 2;
		const w = Math.max(bandWidth - GAP, 1);
		const base = PAD.top + innerHeight;
		const h = barHeight(count);
		if (h <= 0) return '';
		const r = Math.min(4, w / 2, h);
		return [
			`M ${x} ${base}`,
			`L ${x} ${base - h + r}`,
			`Q ${x} ${base - h} ${x + r} ${base - h}`,
			`L ${x + w - r} ${base - h}`,
			`Q ${x + w} ${base - h} ${x + w} ${base - h + r}`,
			`L ${x + w} ${base}`,
			'Z'
		].join(' ');
	}

	function markerX(atLabel: string): number | null {
		const i = data.findIndex((d) => d.label === atLabel);
		return i === -1 ? null : barX(i) + bandWidth / 2;
	}

	let labelStep = $derived(Math.max(1, Math.ceil(n / 6)));
	let hovered = $derived(hoverIndex !== null ? data[hoverIndex] : null);
</script>

<div class={className}>
	<div
		bind:clientWidth={containerWidth}
		class="relative"
		style="height: {height}px;"
		role="img"
		aria-label="histogram chart"
		onmouseleave={() => (hoverIndex = null)}
	>
		{#if containerWidth > 0}
			<svg width={containerWidth} {height}>
				<!-- Axis line -->
				<line
					x1={PAD.left}
					x2={containerWidth - PAD.right}
					y1={PAD.top + innerHeight}
					y2={PAD.top + innerHeight}
					stroke="rgb(var(--viz-axis))"
					stroke-width="1"
				/>

				<!-- Bars. The hit target spans the full band height, not just the drawn bar,
				     so a short bar is not a pinpoint target. -->
				{#each data as d, i (d.label + i)}
					<path
						d={barPath(i, d.count)}
						fill={hoverIndex === i ? 'rgb(var(--viz-1))' : 'rgb(var(--viz-1) / 0.75)'}
						role="presentation"
					/>
					<rect
						x={barX(i)}
						y={PAD.top}
						width={bandWidth}
						height={innerHeight}
						fill="transparent"
						onmouseenter={() => (hoverIndex = i)}
						role="presentation"
					/>
				{/each}

				<!-- X-axis labels -->
				{#each data as d, i (d.label + i + '-lbl')}
					{#if i % labelStep === 0 || i === data.length - 1}
						<text
							x={barX(i) + bandWidth / 2}
							y={height - 6}
							text-anchor="middle"
							class="fill-fg-subtle text-2xs font-mono tabular-nums"
						>
							{d.label}
						</text>
					{/if}
				{/each}

				<!-- Markers (p50/p95, etc.) -->
				{#each markers as marker (marker.label)}
					{@const mx = markerX(marker.at)}
					{#if mx !== null}
						<line
							x1={mx}
							x2={mx}
							y1={PAD.top}
							y2={PAD.top + innerHeight}
							stroke="rgb(var(--signal))"
							stroke-width="1.5"
						/>
						<text
							x={mx}
							y={PAD.top - 6}
							text-anchor="middle"
							class="fill-signal text-2xs font-mono font-medium"
						>
							{marker.label}
						</text>
					{/if}
				{/each}
			</svg>

			{#if hovered}
				<div
					class="pointer-events-none absolute top-1 left-1 rounded border border-line bg-surface-2 px-2 py-1 text-xs shadow-floating"
				>
					<div class="text-fg-subtle">{hovered.label}</div>
					<div class="font-mono tabular-nums text-fg font-medium">
						{valueFormat(hovered.count)}
					</div>
				</div>
			{/if}
		{/if}
	</div>
</div>
