<script lang="ts">
	/**
	 * PotionUI's "Inlets" mark — three ingredient arcs feeding through inlet dots
	 * into one potion (top-down view). Monochrome via currentColor; never distort,
	 * recolor per-element, or add effects.
	 */
	export let size = 24;
	/** When provided, the mark is exposed to assistive tech as an image with this label. */
	export let label: string | undefined = undefined;
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 48 48"
	fill="none"
	class="flex-shrink-0"
	role={label ? 'img' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : true}
	focusable="false"
>
	<circle cx="24" cy="24" r="8.5" fill="currentColor" />
	<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
		<path d="M27.51 9.93 A14.5 14.5 0 0 1 37.94 28.0" />
		<path d="M34.43 34.07 A14.5 14.5 0 0 1 13.57 34.07" />
		<path d="M10.06 28.0 A14.5 14.5 0 0 1 20.49 9.93" />
	</g>
	<g fill="currentColor">
		<circle cx="24" cy="6" r="1.7" />
		<circle cx="39.6" cy="33" r="1.7" />
		<circle cx="8.4" cy="33" r="1.7" />
	</g>
</svg>
