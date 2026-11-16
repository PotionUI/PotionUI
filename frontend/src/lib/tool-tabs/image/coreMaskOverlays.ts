import MaskBrushOverlay from '$lib/components/tool-tabs/image/MaskBrushOverlay.svelte';
import MaskLassoOverlay from '$lib/components/tool-tabs/image/MaskLassoOverlay.svelte';
import { overlays } from '$lib/editors/overlays';
import { MASK_BRUSH } from './mask';
import { BRUSH_OVERLAY, LASSO_OVERLAY } from './maskOverlays';

let registered = false;

export function ensureCoreMaskOverlays(): void {
	if (registered) return;
	registered = true;
	overlays.register({
		id: BRUSH_OVERLAY,
		editor: 'image',
		component: { component: MaskBrushOverlay },
		cursor: 'crosshair',
		produces: 'selection',
		label: 'Brush',
		order: 10,
		hint: 'Paint over the part you want to change.',
		modes: [
			{ id: 'paint', label: 'Paint', subtract: false },
			{ id: 'erase', label: 'Erase', subtract: true }
		],
		modesLabel: 'Mask brush',
		size: { label: 'Brush size', min: MASK_BRUSH.min, max: MASK_BRUSH.max, step: MASK_BRUSH.step, default: MASK_BRUSH.default, unit: 'px' }
	});
	overlays.register({
		id: LASSO_OVERLAY,
		editor: 'image',
		component: { component: MaskLassoOverlay },
		cursor: 'crosshair',
		produces: 'selection',
		label: 'Lasso',
		order: 20,
		hint: 'Draw around the part you want to change. Release to close the shape.',
		modes: [
			{ id: 'add', label: 'Add', subtract: false },
			{ id: 'subtract', label: 'Subtract', subtract: true }
		],
		modesLabel: 'Lasso mode'
	});
}
