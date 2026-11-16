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

export interface CanvasOverlayEntry extends EditorEntryBase {
	component: ComponentRef;
	cursor?: 'crosshair' | 'brush' | 'default';
	produces?: 'selection';
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
