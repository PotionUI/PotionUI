import { describe, expect, it, vi } from 'vitest';
import { canvasActionsOf, railTools, type CanvasAction, type CanvasActionContext } from '$lib/editors/railTools';
import type { CanvasOverlayEntry } from '$lib/editors/overlays';
import { ensureCoreImageEditor } from './coreEditor';
import {
	handleEscape,
	maskControls,
	type CropControls,
	type ImageCanvasSurface,
	type MaskControls,
	type PaintControls
} from './canvasActions';
import { BRUSH_OVERLAY, LASSO_OVERLAY } from './maskOverlays';
import CropBar from '$lib/components/tool-tabs/image/CropBar.svelte';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';

ensureCoreImageEditor();

function fakeSession() {
	return {
		cutSelection: vi.fn(),
		copySelection: vi.fn(),
		deleteSelection: vi.fn(),
		invertSelection: vi.fn(),
		deselect: vi.fn(),
		flipFloating: vi.fn(),
		placeFloating: vi.fn(),
		cancelSelectionStroke: vi.fn(() => false),
		setTool: vi.fn(),
		setSetting: vi.fn(),
		setColor: vi.fn()
	};
}

function paintControls(state: Record<string, unknown> = {}, session = fakeSession()): PaintControls & { fake: ReturnType<typeof fakeSession> } {
	const snapshot = {
		toolId: 'lasso',
		hasSelection: true,
		floatingId: null,
		activeLayerName: 'Layer',
		activeLayerEmpty: false,
		settings: { size: 24, opacity: 80, color: '#ff0000', tolerance: 32 },
		tools: [
			{ id: 'brush', options: ['size', 'opacity', 'color'] },
			{ id: 'eraser', options: ['size', 'opacity'] },
			{ id: 'fill', options: ['color', 'tolerance'] }
		],
		...state
	};
	return { session: session as never, state: snapshot as never, fake: session };
}

function maskFake(over: Partial<MaskControls> = {}): MaskControls & { calls: Record<string, ReturnType<typeof vi.fn>> } {
	const calls = { chooseTool: vi.fn(), setSubtract: vi.fn(), setBrush: vi.fn(), clear: vi.fn(), invert: vi.fn(), focusPrompt: vi.fn() };
	return {
		tools: [
			{ id: BRUSH_OVERLAY, label: 'Brush' },
			{ id: LASSO_OVERLAY, label: 'Lasso' }
		],
		tool: LASSO_OVERLAY,
		modes: [
			{ id: 'add', label: 'Add', subtract: false },
			{ id: 'subtract', label: 'Subtract', subtract: true }
		],
		subtract: false,
		brush: { label: 'Brush', min: 4, max: 300, step: 1, unit: 'px', value: 64 },
		hasArea: true,
		canFocusPrompt: true,
		...calls,
		...over,
		calls
	};
}

function ctxFor(surface: Partial<ImageCanvasSurface>, tool = 't', phone = false): CanvasActionContext {
	const full: ImageCanvasSurface = { paint: null, mask: null, crop: null, slice: null, editArea: vi.fn(), ...surface };
	return { editor: 'image', tool, phone, host: {} as never, surface: full };
}

function actionsOf(id: string, surface: Partial<ImageCanvasSurface>, phone = false): CanvasAction[] {
	return canvasActionsOf(railTools.get('image', id), ctxFor(surface, id, phone));
}

const byId = (actions: CanvasAction[], id: string) => actions.find((action) => action.id === id)!;

describe('registry', () => {
	it('gives the selection, brush, task, crop and slice tools their actions and leaves the rest bare', () => {
		for (const id of ['paint.rect', 'paint.lasso', 'paint.move', 'paint.brush', 'paint.eraser', 'edit_region', 'remove_object', 'crop', 'slice']) {
			expect(railTools.get('image', id)?.canvasActions, id).toBeTypeOf('function');
		}
		for (const id of ['paint.fill', 'paint.pick', 'resize', 'light']) {
			expect(railTools.get('image', id)?.canvasActions, id).toBeUndefined();
		}
	});
});

describe('selection tools', () => {
	it('offers Cut, Copy, Delete, Invert, Deselect and Edit this area for a finished selection', () => {
		const paint = paintControls();
		const actions = actionsOf('paint.lasso', { paint });
		expect(actions.map((action) => action.id)).toEqual(['cut', 'copy', 'delete', 'invert', 'deselect', 'edit-area']);
		expect(byId(actions, 'edit-area').primary).toBe(true);
		expect(byId(actions, 'deselect').shortcut).toBe('Esc');
	});

	it('runs the same session calls as the panel buttons', () => {
		const paint = paintControls();
		const editArea = vi.fn();
		const actions = actionsOf('paint.rect', { paint, editArea });
		byId(actions, 'cut').run!();
		byId(actions, 'copy').run!();
		byId(actions, 'delete').run!();
		byId(actions, 'invert').run!();
		byId(actions, 'deselect').run!();
		byId(actions, 'edit-area').run!();
		expect(paint.fake.cutSelection).toHaveBeenCalledTimes(1);
		expect(paint.fake.copySelection).toHaveBeenCalledTimes(1);
		expect(paint.fake.deleteSelection).toHaveBeenCalledTimes(1);
		expect(paint.fake.invertSelection).toHaveBeenCalledTimes(1);
		expect(paint.fake.deselect).toHaveBeenCalledTimes(1);
		expect(editArea).toHaveBeenCalledTimes(1);
	});

	it('has nothing to show before a selection exists', () => {
		expect(actionsOf('paint.lasso', { paint: paintControls({ hasSelection: false }) })).toEqual([]);
		expect(actionsOf('paint.lasso', { paint: null })).toEqual([]);
	});

	it('disables Cut and Delete with the reason when the layer is empty', () => {
		const actions = actionsOf('paint.lasso', { paint: paintControls({ activeLayerEmpty: true }) });
		expect(byId(actions, 'cut')).toMatchObject({ disabled: true, reason: 'This layer is empty' });
		expect(byId(actions, 'delete').disabled).toBe(true);
		expect(byId(actions, 'copy').disabled).toBeFalsy();
	});

	it('swaps to Flip horizontal, Delete and Place for a floating piece', () => {
		const paint = paintControls({ floatingId: 4, toolId: 'move' });
		const actions = actionsOf('paint.move', { paint });
		expect(actions.map((action) => action.id)).toEqual(['flip-horizontal', 'delete', 'place']);
		expect(byId(actions, 'place')).toMatchObject({ primary: true, shortcut: 'Enter' });
		byId(actions, 'flip-horizontal').run!();
		byId(actions, 'place').run!();
		byId(actions, 'delete').run!();
		expect(paint.fake.flipFloating).toHaveBeenCalledTimes(1);
		expect(paint.fake.placeFloating).toHaveBeenCalledTimes(1);
		expect(paint.fake.deleteSelection).toHaveBeenCalledTimes(1);
	});
});

describe('brush and eraser', () => {
	it('shows Brush and Eraser as a switch with the current one pressed', () => {
		const actions = actionsOf('paint.brush', { paint: paintControls({ toolId: 'brush' }) });
		expect(byId(actions, 'brush')).toMatchObject({ kind: 'toggle', pressed: true });
		expect(byId(actions, 'eraser')).toMatchObject({ kind: 'toggle', pressed: false });
	});

	it('switches the session tool from the bar', () => {
		const paint = paintControls({ toolId: 'brush' });
		const actions = actionsOf('paint.brush', { paint });
		byId(actions, 'eraser').run!();
		expect(paint.fake.setTool).toHaveBeenCalledWith('eraser');
		const eraserActions = actionsOf('paint.eraser', { paint: paintControls({ toolId: 'eraser' }) });
		expect(byId(eraserActions, 'eraser').pressed).toBe(true);
		expect(byId(eraserActions, 'brush').pressed).toBe(false);
	});

	it('writes size, opacity and colour through the session settings', () => {
		const paint = paintControls({ toolId: 'brush' });
		const actions = actionsOf('paint.brush', { paint });
		expect(byId(actions, 'size').value).toMatchObject({ value: 24, type: 'range' });
		byId(actions, 'size').value!.set(40);
		byId(actions, 'opacity').value!.set(55);
		byId(actions, 'colour').value!.set('#00ff00');
		expect(paint.fake.setSetting).toHaveBeenCalledWith('size', 40);
		expect(paint.fake.setSetting).toHaveBeenCalledWith('opacity', 55);
		expect(paint.fake.setColor).toHaveBeenCalledWith('#00ff00');
	});

	it('leaves out the colour for the eraser, which has none', () => {
		const actions = actionsOf('paint.eraser', { paint: paintControls({ toolId: 'eraser' }) });
		expect(actions.map((action) => action.id)).toEqual(['brush', 'eraser', 'size', 'opacity']);
	});
});

describe('marking tools', () => {
	it('lists the tool and mode switches, brush size, Invert, Clear area and Edit this area', () => {
		const mask = maskFake();
		const actions = actionsOf('edit_region', { mask });
		expect(actions.map((action) => action.id)).toEqual([`tool:${BRUSH_OVERLAY}`, `tool:${LASSO_OVERLAY}`, 'mode:add', 'mode:subtract', 'size', 'invert', 'clear', 'edit-area']);
		expect(byId(actions, `tool:${LASSO_OVERLAY}`).pressed).toBe(true);
		expect(byId(actions, 'mode:add').pressed).toBe(true);
		expect(byId(actions, 'mode:subtract').pressed).toBe(false);
	});

	it('calls the same handlers the panel uses', () => {
		const mask = maskFake();
		const actions = actionsOf('remove_object', { mask });
		byId(actions, `tool:${BRUSH_OVERLAY}`).run!();
		byId(actions, 'mode:subtract').run!();
		byId(actions, 'size').value!.set(120);
		byId(actions, 'invert').run!();
		byId(actions, 'clear').run!();
		byId(actions, 'edit-area').run!();
		expect(mask.calls.chooseTool).toHaveBeenCalledWith(BRUSH_OVERLAY);
		expect(mask.calls.setSubtract).toHaveBeenCalledWith(true);
		expect(mask.calls.setBrush).toHaveBeenCalledWith(120);
		expect(mask.calls.invert).toHaveBeenCalledTimes(1);
		expect(mask.calls.clear).toHaveBeenCalledTimes(1);
		expect(mask.calls.focusPrompt).toHaveBeenCalledTimes(1);
	});

	it('disables Invert and Clear until something is marked, and skips Edit this area when no prompt is taken', () => {
		const actions = actionsOf('remove_object', { mask: maskFake({ hasArea: false, canFocusPrompt: false }) });
		expect(byId(actions, 'invert')).toMatchObject({ disabled: true, reason: 'Mark an area first' });
		expect(byId(actions, 'clear').disabled).toBe(true);
		expect(actions.find((action) => action.id === 'edit-area')).toBeUndefined();
	});

	it('builds its controls from the registered overlays and the current mode', () => {
		const entries = [
			{ id: BRUSH_OVERLAY, label: 'Brush', size: { label: 'Brush', min: 4, max: 300, step: 1, default: 48, unit: 'px' } },
			{ id: LASSO_OVERLAY, label: 'Lasso', modes: [{ id: 'add', label: 'Add', subtract: false }, { id: 'sub', label: 'Subtract', subtract: true }] }
		] as unknown as CanvasOverlayEntry[];
		const onErase = vi.fn();
		const base = { entries, erase: true, brush: 90, hasArea: true, canFocusPrompt: true, onBrush: vi.fn(), onErase, onClear: vi.fn(), onInvert: vi.fn(), onChoose: vi.fn() };
		const brush = maskControls({ ...base, state: { overlay: BRUSH_OVERLAY } });
		expect(brush.brush).toMatchObject({ value: 90, max: 300 });
		expect(brush.modes).toEqual([]);
		const lasso = maskControls({ ...base, state: { overlay: LASSO_OVERLAY } });
		expect(lasso.brush).toBeNull();
		expect(lasso.subtract).toBe(true);
		lasso.setSubtract(false);
		expect(onErase).toHaveBeenCalledWith(false);
	});
});

describe('crop', () => {
	function crop(over: Partial<CropControls> = {}): CropControls & { change: ReturnType<typeof vi.fn> } {
		return { params: { plan: EMPTY_IMAGE_PLAN, aspect: 'free' }, input: { width: 100, height: 80 }, canApply: true, change: vi.fn(), apply: vi.fn(), cancel: vi.fn(), ...over } as never;
	}

	it('renders the existing crop bar and adds Cancel and Apply crop', () => {
		const controls = crop();
		const actions = actionsOf('crop', { crop: controls });
		expect(actions.map((action) => action.id)).toEqual(['crop-bar', 'cancel', 'apply']);
		const bar = byId(actions, 'crop-bar');
		expect(bar.kind).toBe('custom');
		expect(bar.component).toEqual({ component: CropBar });
		expect(bar.props).toMatchObject({ input: { width: 100, height: 80 }, phone: false, onchange: controls.change });
		byId(actions, 'cancel').run!();
		byId(actions, 'apply').run!();
		expect(controls.cancel).toHaveBeenCalledTimes(1);
		expect(controls.apply).toHaveBeenCalledTimes(1);
	});

	it('disables Apply crop when there is nothing to apply and passes the phone flag on', () => {
		const actions = actionsOf('crop', { crop: crop({ canApply: false }) }, true);
		expect(byId(actions, 'apply').disabled).toBe(true);
		expect(byId(actions, 'crop-bar').props).toMatchObject({ phone: true });
	});

	it('has nothing before the picture is ready', () => {
		expect(actionsOf('crop', { crop: null })).toEqual([]);
	});
});

describe('slice', () => {
	it('runs Detect and Clear and locks both while slicing runs', () => {
		const slice = { locked: false, hasCells: true, detect: vi.fn(), clear: vi.fn() };
		const actions = actionsOf('slice', { slice });
		byId(actions, 'detect').run!();
		byId(actions, 'clear').run!();
		expect(slice.detect).toHaveBeenCalledTimes(1);
		expect(slice.clear).toHaveBeenCalledTimes(1);
		const locked = actionsOf('slice', { slice: { ...slice, locked: true } });
		expect(byId(locked, 'detect').disabled).toBe(true);
		expect(byId(locked, 'clear').disabled).toBe(true);
		expect(byId(actionsOf('slice', { slice: { ...slice, hasCells: false } }), 'clear').disabled).toBe(true);
	});
});

describe('Escape', () => {
	it('cancels a selection stroke first', () => {
		const paint = paintControls();
		paint.fake.cancelSelectionStroke.mockReturnValue(true);
		expect(handleEscape({ mode: 'paint', paint, cropPending: false, cancelCrop: vi.fn() })).toBe(true);
		expect(paint.fake.deselect).not.toHaveBeenCalled();
	});

	it('deselects a finished selection when no stroke is running', () => {
		const paint = paintControls();
		expect(handleEscape({ mode: 'paint', paint, cropPending: false, cancelCrop: vi.fn() })).toBe(true);
		expect(paint.fake.deselect).toHaveBeenCalledTimes(1);
	});

	it('leaves the key alone when there is nothing to cancel', () => {
		const paint = paintControls({ hasSelection: false });
		expect(handleEscape({ mode: 'paint', paint, cropPending: false, cancelCrop: vi.fn() })).toBe(false);
		expect(handleEscape({ mode: 'paint', paint: null, cropPending: false, cancelCrop: vi.fn() })).toBe(false);
		expect(handleEscape({ mode: 'mask', paint: null, cropPending: false, cancelCrop: vi.fn() })).toBe(false);
		expect(handleEscape({ mode: 'crop', paint: null, cropPending: false, cancelCrop: vi.fn() })).toBe(false);
	});

	it('cancels a pending crop', () => {
		const cancelCrop = vi.fn();
		expect(handleEscape({ mode: 'crop', paint: null, cropPending: true, cancelCrop })).toBe(true);
		expect(cancelCrop).toHaveBeenCalledTimes(1);
	});
});
