import type { Component } from 'svelte';

export type EditorKind = 'image' | 'video' | 'audio';

export const EDITOR_CONTRACT = 1;

export interface PixelSize {
	width: number;
	height: number;
}

export type ComponentRef = { component: Component<any> } | { pluginId: string; path: string };

export interface EditorEntryBase {
	id: string;
	editor: EditorKind;
	contract?: number;
	pluginId?: string;
	overridable?: boolean;
}
