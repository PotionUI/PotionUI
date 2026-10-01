import { describe, expect, it, vi } from 'vitest';
import {
	createImageEditorApi,
	imageSources,
	paintFilters,
	pluginTools,
	setActiveEditor
} from './registries';
import type { PaintFilter, PaintTool } from './types';

const tool: PaintTool = { id: 'line', label: 'Line', icon: 'M0 0', group: 'shapes' };
const filter: PaintFilter = {
	id: 'pixelate',
	label: 'Pixelate',
	params: [],
	active: () => false,
	apply: (image) => image
};

describe('plugin registries', () => {
	it('ships the built-in filters', () => {
		expect(paintFilters.list().map((f) => f.id)).toEqual(
			expect.arrayContaining(['tone', 'invert', 'grayscale'])
		);
	});

	it('registers and removes a tool through the plugin api', () => {
		const api = createImageEditorApi();
		const off = api.registerTool(tool);
		expect(pluginTools.has('line')).toBe(true);
		off();
		expect(pluginTools.has('line')).toBe(false);
	});

	it('registers filters and image sources through the plugin api', () => {
		const api = createImageEditorApi();
		const offFilter = api.registerFilter(filter);
		const offSource = api.registerImageSource({
			id: 'stock',
			label: 'Stock',
			pick: async () => null
		});
		expect(paintFilters.has('pixelate')).toBe(true);
		expect(imageSources.has('stock')).toBe(true);
		offFilter();
		offSource();
		expect(paintFilters.has('pixelate')).toBe(false);
		expect(imageSources.has('stock')).toBe(false);
	});

	it('reports no editor open and refuses image calls until one is attached', () => {
		const api = createImageEditorApi();
		setActiveEditor(null);
		expect(api.isOpen()).toBe(false);
		expect(api.addImage({} as HTMLCanvasElement, 'x')).toBe(false);
		expect(api.openImage({} as HTMLCanvasElement, 'x')).toBe(false);
	});

	it('forwards image calls to the open editor', () => {
		const api = createImageEditorApi();
		const addImage = vi.fn();
		const openImage = vi.fn();
		setActiveEditor({ flatten: () => ({}) as HTMLCanvasElement, addImage, openImage });
		const canvas = {} as HTMLCanvasElement;
		expect(api.addImage(canvas, 'a.png')).toBe(true);
		expect(api.openImage(canvas, 'b.png')).toBe(true);
		expect(addImage).toHaveBeenCalledWith(canvas, 'a.png');
		expect(openImage).toHaveBeenCalledWith(canvas, 'b.png');
		setActiveEditor(null);
	});
});
