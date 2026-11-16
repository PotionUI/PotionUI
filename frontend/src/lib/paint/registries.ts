import { BUILTIN_FILTERS } from './adjustments/builtin';
import { Registry } from './registry';
import type { EditorApi, ImageSource, PaintAction, PaintFilter, PaintTool } from './types';

export const pluginTools = new Registry<PaintTool>();
export const paintFilters = new Registry<PaintFilter>();
export const imageSources = new Registry<ImageSource>();
export const paintActions = new Registry<PaintAction>();

for (const filter of BUILTIN_FILTERS) paintFilters.register(filter);

let activeEditor: EditorApi | null = null;

export function setActiveEditor(editor: EditorApi | null): void {
	activeEditor = editor;
}

export interface PluginImageEditorApi {
	registerTool(tool: PaintTool): () => void;
	registerAdjustment(adjustment: PaintFilter): () => void;
	registerFilter(filter: PaintFilter): () => void;
	registerImageSource(source: ImageSource): () => void;
	registerAction(action: PaintAction): () => void;
	addImage(canvas: HTMLCanvasElement, name: string): boolean;
	openImage(canvas: HTMLCanvasElement, name: string): boolean;
	isOpen(): boolean;
}

export function createImageEditorApi(): PluginImageEditorApi {
	return {
		registerTool: (tool) => pluginTools.register(tool),
		registerAdjustment: (adjustment) => paintFilters.register(adjustment),
		registerFilter: (filter) => paintFilters.register(filter),
		registerImageSource: (source) => imageSources.register(source),
		registerAction: (action) => paintActions.register(action),
		addImage(canvas, name) {
			if (!activeEditor) return false;
			activeEditor.addImage(canvas, name);
			return true;
		},
		openImage(canvas, name) {
			if (!activeEditor) return false;
			activeEditor.openImage(canvas, name);
			return true;
		},
		isOpen: () => activeEditor !== null
	};
}
