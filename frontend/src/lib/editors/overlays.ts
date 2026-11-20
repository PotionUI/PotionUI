import type { Readable } from 'svelte/store';
import type { Viewport } from '$lib/tool-tabs/frame/viewport';
import { createEditorRegistry } from './registry';
import type { ComponentRef, EditorEntryBase } from './types';

export interface NormRect {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface Selection {
	mask: { item_id: string; url: string } | null;
	geometry: string;
	bounds: NormRect | null;
}

export interface OverlayMode {
	id: string;
	label: string;
	subtract: boolean;
}

export interface OverlaySizeSetting {
	label: string;
	min: number;
	max: number;
	step: number;
	default: number;
	unit: string;
}

export interface CanvasOverlayEntry extends EditorEntryBase {
	component: ComponentRef;
	cursor?: 'crosshair' | 'brush' | 'default';
	produces?: 'selection';
	label?: string;
	order?: number;
	hint?: string;
	modes?: OverlayMode[];
	modesLabel?: string;
	size?: OverlaySizeSetting;
}

export interface OverlayProps {
	width: number;
	height: number;
	viewport: Readable<Viewport>;
	selection: Readable<Selection | null>;
	commitSelection(mask: Blob | null): Promise<void>;
	phone: boolean;
}

export const overlays = createEditorRegistry<CanvasOverlayEntry>('overlays');
