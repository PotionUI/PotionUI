import { afterEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { overlays, type CanvasOverlayEntry } from '$lib/editors/overlays';
import { pluginOwner } from '$lib/editors/registry';
import {
	BRUSH_OVERLAY,
	LASSO_OVERLAY,
	activeOverlay,
	chooseOverlay,
	maskToolEntries,
	maskToolState,
	modeFor,
	overlayLabel,
	resetMaskTool
} from './maskOverlays';

const component = { component: {} as never };

const brush: CanvasOverlayEntry = {
	id: BRUSH_OVERLAY,
	editor: 'image',
	component,
	produces: 'selection',
	label: 'Brush',
	order: 10,
	modes: [
		{ id: 'paint', label: 'Paint', subtract: false },
		{ id: 'erase', label: 'Erase', subtract: true }
	]
};
const lasso: CanvasOverlayEntry = { id: LASSO_OVERLAY, editor: 'image', component, produces: 'selection', label: 'Lasso', order: 20 };

const stops: Array<() => void> = [];

function register(entry: CanvasOverlayEntry, owner?: string) {
	stops.push(overlays.register(entry, owner));
}

afterEach(() => {
	stops.splice(0).forEach((stop) => stop());
	resetMaskTool();
});

describe('the mask tool list comes from the overlay registry', () => {
	it('lists the overlays that produce a selection, in order', () => {
		register(lasso);
		register(brush);
		register({ id: 'acme.badge', editor: 'image', component }, pluginOwner('acme'));
		expect(maskToolEntries().map((entry) => entry.id)).toEqual([BRUSH_OVERLAY, LASSO_OVERLAY]);
	});

	it('shows a third overlay with no change to the tool, and drops it when it is removed', () => {
		register(brush);
		register(lasso);
		register({ id: 'acme.magic-select', editor: 'image', component, produces: 'selection' }, pluginOwner('acme'));
		const entries = maskToolEntries();
		expect(entries.map((entry) => entry.id)).toEqual([BRUSH_OVERLAY, LASSO_OVERLAY, 'acme.magic-select']);
		expect(overlayLabel(entries[2])).toBe('Magic select');
		stops.pop()!();
		expect(maskToolEntries()).toHaveLength(2);
	});

	it('uses a label the overlay gives over one made from its id', () => {
		expect(overlayLabel({ id: 'acme.pick', editor: 'image', component, label: 'Smart pick' })).toBe('Smart pick');
	});

	it('picks the chosen overlay and falls back to the first when it is gone', () => {
		register(brush);
		register(lasso);
		const entries = maskToolEntries();
		expect(activeOverlay(entries, get(maskToolState))?.id).toBe(BRUSH_OVERLAY);
		chooseOverlay(LASSO_OVERLAY);
		expect(activeOverlay(entries, get(maskToolState))?.id).toBe(LASSO_OVERLAY);
		chooseOverlay('acme.gone');
		expect(activeOverlay(entries, get(maskToolState))?.id).toBe(BRUSH_OVERLAY);
		expect(activeOverlay([], get(maskToolState))).toBeNull();
	});

	it('maps the subtract flag to the overlay mode, and has none for an overlay without modes', () => {
		expect(modeFor(brush, false)?.id).toBe('paint');
		expect(modeFor(brush, true)?.id).toBe('erase');
		expect(modeFor(lasso, true)).toBeNull();
	});
});
