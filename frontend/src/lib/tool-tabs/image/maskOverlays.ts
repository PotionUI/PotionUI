import { writable } from 'svelte/store';
import { guard } from '$lib/editors/guard';
import { overlays, type CanvasOverlayEntry, type OverlayMode } from '$lib/editors/overlays';
import { entryKey } from '$lib/editors/registry';
import type { Point } from '$lib/paint/types';
import type { Rgb } from './mask';

export const BRUSH_OVERLAY = 'mask.brush';
export const LASSO_OVERLAY = 'mask.lasso';

export interface MaskSurface {
	element: HTMLCanvasElement;
	context(): CanvasRenderingContext2D | null;
	tint: Rgb;
	tintCss: string;
	toPicture(clientX: number, clientY: number): Point | null;
	scale(): number;
	changed(): void;
}

export interface MaskOverlayProps {
	surface: MaskSurface;
	width: number;
	height: number;
	phone: boolean;
	size: number;
	subtract: boolean;
	disabled: boolean;
	commitSelection(mask: Blob | null): Promise<void>;
}

export interface MaskToolState {
	overlay: string;
}

export const maskToolState = writable<MaskToolState>({ overlay: BRUSH_OVERLAY });

export const maskArea = writable<{ width: number; height: number } | null>(null);

export function maskToolEntries(): CanvasOverlayEntry[] {
	return overlays
		.list('image')
		.filter((entry) => guard(entryKey('image', entry.id), 'read the marking tool', () => entry.produces === 'selection', false))
		.sort((a, b) => (a.order ?? 1000) - (b.order ?? 1000) || a.id.localeCompare(b.id));
}

export function overlayLabel(entry: CanvasOverlayEntry): string {
	if (entry.label) return entry.label;
	const last = entry.id.split(/[.:/]/).pop() ?? entry.id;
	const words = last.replace(/[-_]+/g, ' ').trim();
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : entry.id;
}

export function activeOverlay(entries: readonly CanvasOverlayEntry[], state: MaskToolState): CanvasOverlayEntry | null {
	return entries.find((entry) => entry.id === state.overlay) ?? entries[0] ?? null;
}

export function modeFor(entry: CanvasOverlayEntry | null, subtract: boolean): OverlayMode | null {
	if (!entry?.modes?.length) return null;
	return entry.modes.find((mode) => mode.subtract === subtract) ?? entry.modes[0];
}

export function chooseOverlay(id: string): void {
	maskToolState.set({ overlay: id });
}

export function resetMaskTool(): void {
	maskToolState.set({ overlay: BRUSH_OVERLAY });
	maskArea.set(null);
}

export function maskHintText(entry: CanvasOverlayEntry | null, what: string | null = null): string | null {
	if (!entry?.hint) return null;
	return what ? entry.hint.replace('part you want to change', what) : entry.hint;
}

export const QUICK_BRUSH_HINT = 'Paint over what the model should repaint. Everything you leave alone is kept.';

export function quickMaskHint(entry: CanvasOverlayEntry | null): string {
	if (!entry || entry.id === BRUSH_OVERLAY) return QUICK_BRUSH_HINT;
	return entry.hint ?? QUICK_BRUSH_HINT;
}
