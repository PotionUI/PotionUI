import type { PixelBuffer, Rect, Rgba } from '../types';

export function floodFill(
	buffer: PixelBuffer,
	startX: number,
	startY: number,
	color: Rgba,
	tolerance: number,
	mask?: Uint8ClampedArray
): Rect | null {
	const { width, height, data } = buffer;
	const sx = Math.floor(startX);
	const sy = Math.floor(startY);
	if (sx < 0 || sy < 0 || sx >= width || sy >= height) return null;

	const start = (sy * width + sx) * 4;
	const target = [data[start], data[start + 1], data[start + 2], data[start + 3]];
	if (
		target[0] === color[0] &&
		target[1] === color[1] &&
		target[2] === color[2] &&
		target[3] === color[3]
	) {
		return null;
	}

	const limit = (Math.min(100, Math.max(0, tolerance)) / 100) * 255 * 4;
	const visited = new Uint8Array(width * height);
	const reaches = (pixel: number): boolean => {
		const i = pixel * 4;
		return (
			Math.abs(data[i] - target[0]) +
				Math.abs(data[i + 1] - target[1]) +
				Math.abs(data[i + 2] - target[2]) +
				Math.abs(data[i + 3] - target[3]) <=
			limit
		);
	};

	const stack: number[] = [sx, sy];
	let minX = width;
	let minY = height;
	let maxX = -1;
	let maxY = -1;
	const region: number[] = [];

	while (stack.length > 0) {
		const y = stack.pop() as number;
		const x = stack.pop() as number;
		let left = x;
		while (left >= 0 && !visited[y * width + left] && reaches(y * width + left)) left--;
		left++;

		let above = false;
		let below = false;
		let cursor = left;
		while (cursor < width && !visited[y * width + cursor] && reaches(y * width + cursor)) {
			const pixel = y * width + cursor;
			visited[pixel] = 1;
			region.push(pixel);

			if (y > 0) {
				const up = (y - 1) * width + cursor;
				const open = !visited[up] && reaches(up);
				if (open && !above) {
					stack.push(cursor, y - 1);
					above = true;
				} else if (!open) {
					above = false;
				}
			}
			if (y < height - 1) {
				const down = (y + 1) * width + cursor;
				const open = !visited[down] && reaches(down);
				if (open && !below) {
					stack.push(cursor, y + 1);
					below = true;
				} else if (!open) {
					below = false;
				}
			}
			cursor++;
		}
	}

	for (const pixel of region) {
		if (mask && mask[pixel] < 128) continue;
		const i = pixel * 4;
		data[i] = color[0];
		data[i + 1] = color[1];
		data[i + 2] = color[2];
		data[i + 3] = color[3];
		const x = pixel % width;
		const y = (pixel - x) / width;
		if (x < minX) minX = x;
		if (x > maxX) maxX = x;
		if (y < minY) minY = y;
		if (y > maxY) maxY = y;
	}

	if (maxX < 0) return null;
	return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
}
