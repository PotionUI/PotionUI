<script lang="ts">
	import dismiss from '$lib/actions/dismiss';
	import type { Point } from '$lib/paint/types';
	import { closedShape, extendOutline, LASSO_MIN_STEP, stampShape } from '$lib/tool-tabs/image/maskRegion';
	import type { MaskOverlayProps } from '$lib/tool-tabs/image/maskOverlays';

	let { surface, width, height, subtract, disabled }: MaskOverlayProps = $props();

	let points: Point[] = [];
	let drawing = $state(false);
	let frame = 0;
	let outline = $state('');

	const nextFrame = (run: () => void): number =>
		typeof requestAnimationFrame === 'function' ? requestAnimationFrame(run) : (setTimeout(run, 16) as unknown as number);
	const stopFrame = (handle: number) => (typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame(handle) : clearTimeout(handle));

	function redraw() {
		frame = 0;
		outline = points.map((point) => `${point.x},${point.y}`).join(' ');
	}

	function reset() {
		points = [];
		drawing = false;
		if (frame) stopFrame(frame);
		frame = 0;
		outline = '';
	}

	function down(event: PointerEvent) {
		if (disabled || event.button !== 0) return;
		const at = surface.toPicture(event.clientX, event.clientY);
		if (!at) return;
		event.preventDefault();
		surface.element.setPointerCapture?.(event.pointerId);
		points = [at];
		drawing = true;
	}

	function move(event: PointerEvent) {
		if (!drawing) return;
		const at = surface.toPicture(event.clientX, event.clientY);
		if (!at) return;
		if (extendOutline(points, at, LASSO_MIN_STEP * surface.scale()) && !frame) frame = nextFrame(redraw);
	}

	function up(event: PointerEvent) {
		if (!drawing) return;
		const at = surface.toPicture(event.clientX, event.clientY);
		if (at) extendOutline(points, at, LASSO_MIN_STEP * surface.scale());
		const shape = closedShape(points);
		reset();
		if (!shape) return;
		const context = surface.context();
		const { width: columns, height: rows } = surface.element;
		if (!context) return;
		const image = context.getImageData(0, 0, columns, rows);
		if (stampShape(image.data, columns, rows, shape, subtract, surface.tint) === 0) return;
		context.putImageData(image, 0, 0);
		surface.changed();
	}

	$effect(() => {
		const element = surface.element;
		element.addEventListener('pointerdown', down);
		element.addEventListener('pointermove', move);
		element.addEventListener('pointerup', up);
		element.addEventListener('pointercancel', reset);
		return () => {
			element.removeEventListener('pointerdown', down);
			element.removeEventListener('pointermove', move);
			element.removeEventListener('pointerup', up);
			element.removeEventListener('pointercancel', reset);
			if (frame) stopFrame(frame);
		};
	});
</script>

{#if drawing}
	<span hidden use:dismiss={{ onDismiss: reset, outside: false }}></span>
{/if}
{#if outline}
	<svg
		class="pointer-events-none absolute inset-0 h-full w-full"
		viewBox="0 0 {width} {height}"
		preserveAspectRatio="none"
		aria-hidden="true"
		data-lasso-outline
	>
		<polygon points={outline} class="lasso-shape" />
	</svg>
{/if}

<style>
	.lasso-shape {
		fill: rgb(var(--signal) / 0.2);
		stroke: rgb(var(--signal));
		stroke-width: 1.5;
		stroke-linejoin: round;
		vector-effect: non-scaling-stroke;
	}
</style>
