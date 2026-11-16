<script lang="ts">
	import { drawSegment } from '$lib/paint/raster/brush';
	import type { Point } from '$lib/paint/types';
	import type { MaskOverlayProps } from '$lib/tool-tabs/image/maskOverlays';

	let { surface, size, subtract, disabled }: MaskOverlayProps = $props();

	let painting = false;
	let last: Point | null = null;

	function stroke(to: Point) {
		const context = surface.context();
		if (!context) return;
		drawSegment(context, last ?? to, to, { size: size * surface.scale(), color: surface.tintCss, erase: subtract });
		last = to;
	}

	function down(event: PointerEvent) {
		if (disabled || event.button !== 0) return;
		const at = surface.toPicture(event.clientX, event.clientY);
		if (!at) return;
		event.preventDefault();
		surface.element.setPointerCapture?.(event.pointerId);
		painting = true;
		last = null;
		stroke(at);
	}

	function move(event: PointerEvent) {
		if (!painting) return;
		const at = surface.toPicture(event.clientX, event.clientY);
		if (at) stroke(at);
	}

	function up() {
		if (!painting) return;
		painting = false;
		last = null;
		surface.changed();
	}

	$effect(() => {
		const element = surface.element;
		element.addEventListener('pointerdown', down);
		element.addEventListener('pointermove', move);
		element.addEventListener('pointerup', up);
		element.addEventListener('pointercancel', up);
		return () => {
			element.removeEventListener('pointerdown', down);
			element.removeEventListener('pointermove', move);
			element.removeEventListener('pointerup', up);
			element.removeEventListener('pointercancel', up);
		};
	});
</script>
