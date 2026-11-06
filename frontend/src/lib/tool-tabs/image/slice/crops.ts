import type { Analysis } from './detect';
import { outputSize, type Cell, type SliceOutput } from './model';

export interface Box {
	x: number;
	y: number;
	width: number;
	height: number;
}

export interface CropPlan {
	width: number;
	height: number;
	src: Box;
	dst: Box;
}

export function cropPlan(
	an: Analysis | null,
	cell: Cell,
	output: SliceOutput,
	logical: { width: number; height: number },
	source: { width: number; height: number },
	maxSide: number
): CropPlan {
	const size = outputSize(an, cell, output, logical);
	const scale = Math.min(1, maxSide / Math.max(size.width, size.height));
	const width = Math.max(1, Math.round(size.width * scale));
	const height = Math.max(1, Math.round(size.height * scale));
	const r = size.rect;
	const src = { x: r.x0 * source.width, y: r.y0 * source.height, width: (r.x1 - r.x0) * source.width, height: (r.y1 - r.y0) * source.height };
	const dw = (r.x1 - r.x0) * logical.width * scale;
	const dh = (r.y1 - r.y0) * logical.height * scale;
	return { width, height, src, dst: { x: (width - dw) / 2, y: (height - dh) / 2, width: dw, height: dh } };
}

export function cropCanvas(
	source: HTMLCanvasElement,
	an: Analysis | null,
	cell: Cell,
	output: SliceOutput,
	logical: { width: number; height: number },
	maxSide: number
): HTMLCanvasElement {
	const plan = cropPlan(an, cell, output, logical, source, maxSide);
	const canvas = document.createElement('canvas');
	canvas.width = plan.width;
	canvas.height = plan.height;
	const context = canvas.getContext('2d');
	if (!context) return canvas;
	if (an && !an.transparent) {
		context.fillStyle = `rgb(${an.bg.join(',')})`;
		context.fillRect(0, 0, canvas.width, canvas.height);
	}
	context.drawImage(source, plan.src.x, plan.src.y, plan.src.width, plan.src.height, plan.dst.x, plan.dst.y, plan.dst.width, plan.dst.height);
	return canvas;
}

const CRC_TABLE = (() => {
	const table = new Uint32Array(256);
	for (let n = 0; n < 256; n++) {
		let c = n;
		for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
		table[n] = c >>> 0;
	}
	return table;
})();

export function crc32(bytes: Uint8Array): number {
	let crc = 0xffffffff;
	for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
	return (crc ^ 0xffffffff) >>> 0;
}

const UTF8_NAMES = 0x0800;

export function zipStore(files: readonly { name: string; data: Uint8Array }[]): Uint8Array {
	const encoder = new TextEncoder();
	const chunks: Uint8Array[] = [];
	const central: Uint8Array[] = [];
	let offset = 0;
	for (const file of files) {
		const name = encoder.encode(file.name);
		const crc = crc32(file.data);
		const local = new Uint8Array(30 + name.length);
		const view = new DataView(local.buffer);
		view.setUint32(0, 0x04034b50, true);
		view.setUint16(4, 20, true);
		view.setUint16(6, UTF8_NAMES, true);
		view.setUint16(8, 0, true);
		view.setUint32(14, crc, true);
		view.setUint32(18, file.data.length, true);
		view.setUint32(22, file.data.length, true);
		view.setUint16(26, name.length, true);
		local.set(name, 30);
		const entry = new Uint8Array(46 + name.length);
		const cv = new DataView(entry.buffer);
		cv.setUint32(0, 0x02014b50, true);
		cv.setUint16(4, 20, true);
		cv.setUint16(6, 20, true);
		cv.setUint16(8, UTF8_NAMES, true);
		cv.setUint32(16, crc, true);
		cv.setUint32(20, file.data.length, true);
		cv.setUint32(24, file.data.length, true);
		cv.setUint16(28, name.length, true);
		cv.setUint32(42, offset, true);
		entry.set(name, 46);
		chunks.push(local, file.data);
		central.push(entry);
		offset += local.length + file.data.length;
	}
	const centralSize = central.reduce((sum, entry) => sum + entry.length, 0);
	const end = new Uint8Array(22);
	const ev = new DataView(end.buffer);
	ev.setUint32(0, 0x06054b50, true);
	ev.setUint16(8, files.length, true);
	ev.setUint16(10, files.length, true);
	ev.setUint32(12, centralSize, true);
	ev.setUint32(16, offset, true);
	const out = new Uint8Array(offset + centralSize + 22);
	let at = 0;
	for (const part of [...chunks, ...central, end]) {
		out.set(part, at);
		at += part.length;
	}
	return out;
}
