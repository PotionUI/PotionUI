import { deflateSync } from 'node:zlib';
import { chunk } from './mediaFieldHelpers';

export const WIDTH = 960;
export const HEIGHT = 640;

export const PANELS: [number, number, number, number, [number, number, number]][] = [
	[24, 24, 300, 300, [60, 90, 150]],
	[318, 24, 610, 300, [70, 140, 120]],
	[644, 24, 936, 300, [120, 90, 160]],
	[24, 330, 610, 616, [210, 140, 110]],
	[644, 330, 936, 460, [170, 120, 80]],
	[644, 482, 936, 616, [240, 240, 238]]
];

export function sheetPng(): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < HEIGHT; y++) {
		const row = Buffer.alloc(1 + WIDTH * 3);
		for (let x = 0; x < WIDTH; x++) {
			const panel = PANELS.find(([x0, y0, x1, y1]) => x >= x0 && x < x1 && y >= y0 && y < y1);
			const [r, g, b] = panel ? panel[4] : [234, 229, 221];
			const shade = panel && (x + y) % 40 < 6 ? 30 : 0;
			row[1 + x * 3] = Math.max(0, r - shade);
			row[2 + x * 3] = Math.max(0, g - shade);
			row[3 + x * 3] = Math.max(0, b - shade);
		}
		rows.push(row);
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(WIDTH, 0);
	header.writeUInt32BE(HEIGHT, 4);
	header[8] = 8;
	header[9] = 2;
	return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}

