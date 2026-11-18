import CropBar from '$lib/components/tool-tabs/image/CropBar.svelte';
import type { CanvasAction, CanvasActionContext } from '$lib/editors/railTools';
import type { CanvasOverlayEntry } from '$lib/editors/overlays';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { selectionTarget } from '$lib/paint/raster/layerContent';
import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
import { activeOverlay, modeFor, overlayLabel, type MaskToolState } from './maskOverlays';
import type { PixelSize } from './payload';
import type { CropParams } from './steps';

export interface PaintControls {
	session: PaintSession;
	state: SessionSnapshot;
}

export interface MaskControls {
	tools: { id: string; label: string }[];
	tool: string;
	modes: { id: string; label: string; subtract: boolean }[];
	subtract: boolean;
	brush: { label: string; min: number; max: number; step: number; unit: string; value: number } | null;
	hasArea: boolean;
	canFocusPrompt: boolean;
	chooseTool(id: string): void;
	setSubtract(subtract: boolean): void;
	setBrush(value: number): void;
	clear(): void;
	invert(): void;
	focusPrompt(): void;
}

export interface CropControls {
	params: CropParams;
	input: PixelSize;
	canApply: boolean;
	change(next: CropParams): void;
	apply(): void;
	cancel(): void;
}

export interface SliceControls {
	locked: boolean;
	hasCells: boolean;
	detect(): void;
	clear(): void;
}

export interface ImageCanvasSurface {
	paint: PaintControls | null;
	mask: MaskControls | null;
	crop: CropControls | null;
	slice: SliceControls | null;
	editArea(): void;
}

export interface MaskControlsInput {
	entries: readonly CanvasOverlayEntry[];
	state: MaskToolState;
	erase: boolean;
	brush: number;
	hasArea: boolean;
	canFocusPrompt: boolean;
	onBrush(value: number): void;
	onErase(erase: boolean): void;
	onClear(): void;
	onInvert(): void;
	onChoose(id: string): void;
}

export function maskControls(input: MaskControlsInput): MaskControls {
	const active = activeOverlay(input.entries, input.state);
	const mode = modeFor(active, input.erase);
	const size = active?.size;
	return {
		tools: input.entries.map((entry) => ({ id: entry.id, label: overlayLabel(entry) })),
		tool: active?.id ?? '',
		modes: (active?.modes ?? []).map(({ id, label, subtract }) => ({ id, label, subtract })),
		subtract: mode?.subtract ?? false,
		brush: size ? { label: size.label, min: size.min, max: size.max, step: size.step, unit: size.unit, value: input.brush } : null,
		hasArea: input.hasArea,
		canFocusPrompt: input.canFocusPrompt,
		chooseTool: input.onChoose,
		setSubtract: input.onErase,
		setBrush: input.onBrush,
		clear: input.onClear,
		invert: input.onInvert,
		focusPrompt: focusDockPrompt
	};
}

export function focusDockPrompt(retries = 12): void {
	if (typeof document === 'undefined') return;
	const field = document.querySelector<HTMLTextAreaElement>('[data-prompt-field]');
	if (field) {
		field.focus();
		return;
	}
	if (retries > 0) requestAnimationFrame(() => focusDockPrompt(retries - 1));
}

function surfaceOf(ctx: CanvasActionContext): ImageCanvasSurface {
	return ctx.surface as ImageCanvasSurface;
}

const ICON = {
	cut: 'scissors',
	copy: 'copy',
	trash: 'trash',
	invert: editorToolIcons.invert,
	deselect: editorToolIcons.rect,
	sparkles: 'sparkles',
	lasso: editorToolIcons.lasso,
	brush: editorToolIcons.brush,
	eraser: editorToolIcons.eraser,
	plus: 'plus',
	minus: 'minus',
	flip: 'flip',
	check: 'check',
	close: 'close',
	detect: 'scan-search',
	refresh: 'refresh'
} as const;

export function selectionActions({ session, state }: PaintControls, editArea: () => void): CanvasAction[] {
	const target = selectionTarget(state.activeLayerName, state.activeLayerEmpty);
	const emptyLayer = target.emptyReason ?? undefined;
	return [
		{ id: 'cut', label: 'Cut', icon: ICON.cut, kind: 'action', group: 'clip', disabled: !!emptyLayer, reason: emptyLayer, run: () => session.cutSelection() },
		{ id: 'copy', label: 'Copy', icon: ICON.copy, kind: 'action', group: 'clip', run: () => session.copySelection() },
		{ id: 'delete', label: 'Delete', icon: ICON.trash, kind: 'action', group: 'clip', disabled: !!emptyLayer, reason: emptyLayer, run: () => session.deleteSelection() },
		{ id: 'invert', label: 'Invert', icon: ICON.invert, kind: 'action', group: 'shape', run: () => void session.invertSelection() },
		{ id: 'deselect', label: 'Deselect', icon: ICON.deselect, shortcut: 'Esc', kind: 'action', group: 'end', run: () => session.deselect() },
		{ id: 'edit-area', label: 'Edit this area', icon: ICON.sparkles, kind: 'action', primary: true, run: editArea }
	];
}

export function floatActions({ session }: PaintControls): CanvasAction[] {
	return [
		{ id: 'flip-horizontal', label: 'Flip horizontal', icon: ICON.flip, kind: 'action', group: 'shape', iconOnly: true, run: () => void session.flipFloating() },
		{ id: 'delete', label: 'Delete', icon: ICON.trash, shortcut: 'Del', kind: 'action', group: 'end', run: () => session.deleteSelection() },
		{ id: 'place', label: 'Place', icon: ICON.check, shortcut: 'Enter', kind: 'action', primary: true, run: () => void session.placeFloating() }
	];
}

export function paintActions({ session, state }: PaintControls): CanvasAction[] {
	const tool = state.tools.find((candidate) => candidate.id === state.toolId);
	const options = tool?.options ?? [];
	const out: CanvasAction[] = [
		{ id: 'brush', label: 'Brush', icon: ICON.brush, shortcut: 'B', kind: 'toggle', group: 'tool', iconOnly: true, pressed: state.toolId === 'brush', run: () => session.setTool('brush') },
		{ id: 'eraser', label: 'Eraser', icon: ICON.eraser, shortcut: 'E', kind: 'toggle', group: 'tool', iconOnly: true, pressed: state.toolId === 'eraser', run: () => session.setTool('eraser') }
	];
	if (options.includes('size')) {
		out.push({ id: 'size', label: 'Size', kind: 'value', group: 'size', value: { type: 'range', value: state.settings.size, min: 1, max: 200, step: 1, unit: 'px', set: (value) => session.setSetting('size', Number(value)) } });
	}
	if (options.includes('opacity')) {
		out.push({ id: 'opacity', label: 'Opacity', kind: 'value', group: 'size', value: { type: 'range', value: state.settings.opacity, min: 1, max: 100, step: 1, unit: '%', set: (value) => session.setSetting('opacity', Number(value)) } });
	}
	if (options.includes('color')) {
		out.push({ id: 'colour', label: 'Colour', kind: 'value', group: 'colour', value: { type: 'colour', value: state.settings.color, set: (value) => session.setColor(String(value)) } });
	}
	return out;
}

export function markActions(mask: MaskControls): CanvasAction[] {
	const out: CanvasAction[] = [];
	if (mask.tools.length > 1) {
		for (const tool of mask.tools) {
			out.push({ id: `tool:${tool.id}`, label: tool.label, icon: tool.id.endsWith('lasso') ? ICON.lasso : tool.id.endsWith('brush') ? ICON.brush : 'editor-mask', kind: 'toggle', group: 'tool', iconOnly: true, pressed: tool.id === mask.tool, run: () => mask.chooseTool(tool.id) });
		}
	}
	for (const mode of mask.modes) {
		out.push({ id: `mode:${mode.id}`, label: mode.label, icon: mode.subtract ? ICON.minus : ICON.plus, kind: 'toggle', group: 'mode', iconOnly: true, pressed: mode.subtract === mask.subtract, run: () => mask.setSubtract(mode.subtract) });
	}
	if (mask.brush) {
		const { label, min, max, step, unit, value } = mask.brush;
		out.push({ id: 'size', label, kind: 'value', group: 'size', value: { type: 'range', value, min, max, step, unit, set: (next) => mask.setBrush(Number(next)) } });
	}
	const needsArea = mask.hasArea ? undefined : 'Mark an area first';
	out.push(
		{ id: 'invert', label: 'Invert', icon: ICON.invert, kind: 'action', group: 'area', disabled: !mask.hasArea, reason: needsArea, run: () => mask.invert() },
		{ id: 'clear', label: 'Clear area', icon: ICON.trash, kind: 'action', group: 'area', disabled: !mask.hasArea, reason: needsArea, run: () => mask.clear() }
	);
	if (mask.canFocusPrompt) out.push({ id: 'edit-area', label: 'Edit this area', icon: ICON.sparkles, kind: 'action', primary: true, run: () => mask.focusPrompt() });
	return out;
}

export function cropActions(crop: CropControls, phone: boolean): CanvasAction[] {
	return [
		{ id: 'crop-bar', label: 'Crop', kind: 'custom', group: 'crop', component: { component: CropBar }, props: { params: crop.params, input: crop.input, phone, onchange: crop.change } },
		{ id: 'cancel', label: 'Cancel', icon: ICON.close, shortcut: 'Esc', kind: 'action', group: 'end', run: crop.cancel },
		{ id: 'apply', label: 'Apply crop', icon: ICON.check, shortcut: 'Enter', kind: 'action', primary: true, disabled: !crop.canApply, reason: crop.canApply ? undefined : 'Nothing to apply yet', run: crop.apply }
	];
}

export function sliceActions(slice: SliceControls): CanvasAction[] {
	const locked = slice.locked ? 'Slicing is running' : undefined;
	return [
		{ id: 'detect', label: 'Detect', icon: ICON.detect, kind: 'action', group: 'slice', disabled: slice.locked, reason: locked, run: slice.detect },
		{ id: 'clear', label: 'Clear', icon: ICON.refresh, kind: 'action', group: 'slice', disabled: slice.locked || !slice.hasCells, reason: locked ?? (slice.hasCells ? undefined : 'No cells to clear'), run: slice.clear }
	];
}

export function selectionToolActions(ctx: CanvasActionContext): CanvasAction[] {
	const surface = surfaceOf(ctx);
	const paint = surface.paint;
	if (!paint) return [];
	if (paint.state.floatingId !== null) return floatActions(paint);
	return paint.state.hasSelection ? selectionActions(paint, surface.editArea) : [];
}

export function brushToolActions(ctx: CanvasActionContext): CanvasAction[] {
	const paint = surfaceOf(ctx).paint;
	return paint ? paintActions(paint) : [];
}

export function taskToolActions(ctx: CanvasActionContext): CanvasAction[] {
	const mask = surfaceOf(ctx).mask;
	return mask ? markActions(mask) : [];
}

export function cropToolActions(ctx: CanvasActionContext): CanvasAction[] {
	const crop = surfaceOf(ctx).crop;
	return crop ? cropActions(crop, ctx.phone) : [];
}

export function sliceToolActions(ctx: CanvasActionContext): CanvasAction[] {
	const slice = surfaceOf(ctx).slice;
	return slice ? sliceActions(slice) : [];
}

export interface EscapeInput {
	mode: 'paint' | 'mask' | 'crop' | 'preview';
	paint: PaintControls | null;
	cropPending: boolean;
	cancelCrop(): void;
}

export function handleEscape(input: EscapeInput): boolean {
	if (input.mode === 'paint') {
		const paint = input.paint;
		if (!paint) return false;
		if (paint.session.cancelSelectionStroke()) return true;
		if (!paint.state.hasSelection) return false;
		paint.session.deselect();
		return true;
	}
	if (input.mode === 'crop' && input.cropPending) {
		input.cancelCrop();
		return true;
	}
	return false;
}
