import { PAINT_ICONS } from '../icons';
import { clearByMask, extractByMask } from '../raster/layerMask';
import { pointInBounds, translatePath } from '../raster/selection';
import { pixelRect } from '../geometry';
import type {
	DocSnapshot,
	Layer,
	PaintTool,
	Point,
	Selection,
	ToolHost,
	ToolPointer
} from '../types';

interface Drag {
	start: Point;
	layer: Layer;
	baseX: number;
	baseY: number;
	basePath: Point[] | null;
	lifted: boolean;
	moved: boolean;
	before: DocSnapshot;
}

function liftSelection(host: ToolHost, selection: Selection, copy: boolean): Layer | null {
	const layer = host.activeLayer;
	if (!layer) return null;
	const context = layer.canvas.getContext('2d');
	const mask = host.layerSelectionMask(layer);
	if (!context || !mask) return null;

	const image = context.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
	const extracted = extractByMask(image.data, mask);
	const scratch = document.createElement('canvas');
	scratch.width = layer.canvas.width;
	scratch.height = layer.canvas.height;
	scratch
		.getContext('2d')
		?.putImageData(
			new ImageData(new Uint8ClampedArray(extracted), image.width, image.height),
			0,
			0
		);

	const piece = document.createElement('canvas');
	piece.width = selection.bounds.width;
	piece.height = selection.bounds.height;
	piece
		.getContext('2d')
		?.drawImage(scratch, layer.x - selection.bounds.x, layer.y - selection.bounds.y);

	if (!copy) {
		const before = host.snapshotLayer(layer);
		clearByMask(image.data, mask);
		context.putImageData(image, 0, 0);
		const rect = pixelRect(
			{
				x: selection.bounds.x - layer.x,
				y: selection.bounds.y - layer.y,
				width: selection.bounds.width,
				height: selection.bounds.height
			},
			layer.canvas
		);
		if (rect) host.commitPixels('Lift selection', layer, rect, before);
	}

	return host.addPieceLayer(piece, selection.bounds.x, selection.bounds.y, 'Piece');
}

export function createMoveTool(): PaintTool {
	let drag: Drag | null = null;
	return {
		id: 'move',
		label: 'Move',
		icon: PAINT_ICONS.move,
		key: 'V',
		group: 'edit',
		panel: 'selection',
		hint: 'Drag to move the active layer. With a selection, the selected pixels lift onto their own layer. Hold Alt to lift a copy. Arrow keys nudge.',
		cursor: 'default',
		pointerDown(host: ToolHost, point: Point, pointer: ToolPointer) {
			const selection = host.selection;
			const lifting = selection !== null && pointInBounds(selection, point);
			host.beginGroup();
			let layer = host.activeLayer;
			if (lifting && selection) layer = liftSelection(host, selection, pointer.altKey);
			if (!layer) {
				host.endGroup('Move', true);
				return;
			}
			drag = {
				start: point,
				layer,
				baseX: layer.x,
				baseY: layer.y,
				basePath: selection ? selection.path.map((p) => ({ ...p })) : null,
				lifted: lifting,
				moved: false,
				before: host.captureDoc()
			};
			host.invalidate();
		},
		pointerMove(host: ToolHost, point: Point) {
			if (!drag) return;
			const dx = Math.round(point.x - drag.start.x);
			const dy = Math.round(point.y - drag.start.y);
			if (dx !== 0 || dy !== 0) drag.moved = true;
			drag.layer.x = drag.baseX + dx;
			drag.layer.y = drag.baseY + dy;
			if (drag.lifted && drag.basePath) {
				host.setSelection(translatePath(drag.basePath, dx, dy), false);
			}
			host.invalidate();
		},
		pointerUp(host: ToolHost, point: Point) {
			if (!drag) return;
			const finished = drag;
			drag = null;
			if (finished.lifted && finished.basePath) {
				const dx = Math.round(point.x - finished.start.x);
				const dy = Math.round(point.y - finished.start.y);
				host.setSelection(translatePath(finished.basePath, dx, dy), true);
			}
			if (!finished.moved && finished.lifted) {
				host.endGroup('Lift selection', true);
				return;
			}
			if (finished.moved) host.commitStructure('Move', finished.before);
			host.endGroup(
				finished.lifted ? 'Lift selection' : 'Move layer',
				!finished.moved && !finished.lifted
			);
		},
		pointerCancel(host: ToolHost) {
			if (!drag) return;
			drag = null;
			host.endGroup('Move', true);
		}
	};
}
