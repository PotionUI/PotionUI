export function isCanvasEmpty(canvas: HTMLCanvasElement): boolean {
	const context = canvas.getContext('2d', { willReadFrequently: true });
	if (!context || canvas.width === 0 || canvas.height === 0) return true;
	const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
	for (let i = 3; i < data.length; i += 4) {
		if (data[i] !== 0) return false;
	}
	return true;
}

export interface SelectionTarget {
	label: string;
	emptyReason: string | null;
}

export function selectionTarget(layerName: string | null, layerEmpty: boolean): SelectionTarget {
	return {
		label: layerName ? `On layer: ${layerName}` : '',
		emptyReason: layerName && layerEmpty ? 'This layer is empty' : null
	};
}
