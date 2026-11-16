export const WORKING_AREA = 1_048_576;

export function isLargeArea(
	area: { width: number; height: number } | null,
	canvas: { width: number; height: number } | null,
	picture: { width: number; height: number } | null
): boolean {
	if (!area || !canvas || !picture || canvas.width <= 0 || canvas.height <= 0) return false;
	const width = area.width * (picture.width / canvas.width);
	const height = area.height * (picture.height / canvas.height);
	const longer = Math.max(width, height);
	return (width + longer) * (height + longer) > 2 * WORKING_AREA;
}

export function areaLabel(area: { width: number; height: number } | null): string {
	return area ? `${area.width}×${area.height}` : '';
}
