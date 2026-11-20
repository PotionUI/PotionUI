import type {
	DocGeometry,
	DocSnapshot,
	Layer,
	LayerState,
	PaintDocument,
	Selection
} from './types';

export function captureDoc(
	doc: PaintDocument,
	selection: Selection | null,
	geometry: DocGeometry
): DocSnapshot {
	return {
		width: doc.width,
		height: doc.height,
		activeIndex: doc.activeIndex,
		selection,
		geometry,
		layers: doc.layers.map((layer) => ({
			layer,
			name: layer.name,
			canvas: layer.canvas,
			x: layer.x,
			y: layer.y,
			visible: layer.visible,
			opacity: layer.opacity,
			source: layer.source
		}))
	};
}

export function restoreDoc(
	doc: PaintDocument,
	snapshot: DocSnapshot
): { selection: Selection | null; geometry: DocGeometry } {
	doc.width = snapshot.width;
	doc.height = snapshot.height;
	doc.activeIndex = snapshot.activeIndex;
	doc.layers = snapshot.layers.map((state: LayerState) => {
		const layer: Layer = state.layer;
		layer.name = state.name;
		layer.canvas = state.canvas;
		layer.x = state.x;
		layer.y = state.y;
		layer.visible = state.visible;
		layer.opacity = state.opacity;
		layer.source = state.source;
		return layer;
	});
	return { selection: snapshot.selection, geometry: snapshot.geometry };
}

function canvasBytes(canvas: { width: number; height: number } | undefined): number {
	return canvas ? canvas.width * canvas.height * 4 : 0;
}

export function structuralBytes(before: DocSnapshot, after: DocSnapshot): number {
	const beforeCanvases = new Set<unknown>();
	const afterCanvases = new Set<unknown>();
	for (const state of before.layers) {
		beforeCanvases.add(state.canvas);
		if (state.source) beforeCanvases.add(state.source);
	}
	for (const state of after.layers) {
		afterCanvases.add(state.canvas);
		if (state.source) afterCanvases.add(state.source);
	}
	let bytes = 0;
	const seen = new Set<unknown>();
	for (const state of [...before.layers, ...after.layers]) {
		for (const canvas of [state.canvas, state.source]) {
			if (!canvas || seen.has(canvas)) continue;
			seen.add(canvas);
			const inBefore = beforeCanvases.has(canvas);
			const inAfter = afterCanvases.has(canvas);
			if (inBefore !== inAfter) bytes += canvasBytes(canvas);
		}
	}
	return bytes + 256;
}
