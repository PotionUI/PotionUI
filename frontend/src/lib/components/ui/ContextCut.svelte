<span class="context-cut" aria-hidden="true" data-testid="context-separator">
	<svg viewBox="0 0 20 48" preserveAspectRatio="none" focusable="false"><path d="M-4.25 -6 L17 24 L-4.25 54"></path></svg>
</span>

<style>
	.context-cut {
		position: relative;
		display: block;
		width: 20px;
		flex-shrink: 0;
		align-self: stretch;
	}

	.context-cut svg {
		position: absolute;
		inset: 0;
		display: block;
		width: 100%;
		height: 100%;
		overflow: visible;
	}

	.context-cut path {
		fill: none;
		stroke: rgb(var(--cut-stroke, var(--surface-1)));
		stroke-width: 3;
		vector-effect: non-scaling-stroke;
	}
</style>
