import { describe, it, expect } from 'vitest';
import { decodeImageDimensions } from './glbImageDimensions';

function bytesFrom(...groups: number[][]): Uint8Array {
	return new Uint8Array(groups.flat());
}

function u32BE(n: number): number[] {
	return [(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff];
}

function u16LE(n: number): number[] {
	return [n & 0xff, (n >>> 8) & 0xff];
}

function u32LE(n: number): number[] {
	return [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];
}

describe('decodeImageDimensions - PNG', () => {
	it('reads width/height from the IHDR chunk', () => {
		const png = bytesFrom(
			[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], // signature
			u32BE(13), // IHDR length (unused by the decoder, but realistic)
			[0x49, 0x48, 0x44, 0x52], // 'IHDR'
			u32BE(1024), // width
			u32BE(768), // height
			[8, 6, 0, 0, 0] // bit depth/color type/etc - not read
		);
		expect(decodeImageDimensions(png, 'image/png')).toEqual({ width: 1024, height: 768 });
	});

	it('returns null when the signature does not match', () => {
		const notPng = new Uint8Array(30);
		expect(decodeImageDimensions(notPng, 'image/png')).toBeNull();
	});

	it('returns null when the chunk after the signature is not IHDR', () => {
		const png = bytesFrom(
			[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
			u32BE(13),
			[0x00, 0x00, 0x00, 0x00], // not 'IHDR'
			u32BE(1),
			u32BE(1)
		);
		expect(decodeImageDimensions(png, 'image/png')).toBeNull();
	});
});

describe('decodeImageDimensions - WebP VP8X (extended)', () => {
	it('reads the 24-bit minus-one width/height fields', () => {
		const webp = bytesFrom(
			[0x52, 0x49, 0x46, 0x46], // 'RIFF'
			u32LE(999), // file size - unused
			[0x57, 0x45, 0x42, 0x50], // 'WEBP'
			[0x56, 0x50, 0x38, 0x58], // 'VP8X'
			u32LE(10), // chunk size - unused
			[0x10, 0, 0, 0], // flags + 3 reserved
			[0xff, 0x03, 0x00], // width - 1 = 1023 -> width 1024
			[0xff, 0x01, 0x00] // height - 1 = 511 -> height 512
		);
		expect(decodeImageDimensions(webp, 'image/webp')).toEqual({ width: 1024, height: 512 });
	});
});

describe('decodeImageDimensions - WebP VP8L (lossless)', () => {
	it('unpacks width-1/height-1 from the 14-bit-each packed field', () => {
		const width = 512;
		const height = 256;
		const packed = ((width - 1) & 0x3fff) | (((height - 1) & 0x3fff) << 14);
		const webp = bytesFrom(
			[0x52, 0x49, 0x46, 0x46],
			u32LE(999),
			[0x57, 0x45, 0x42, 0x50],
			[0x56, 0x50, 0x38, 0x4c], // 'VP8L'
			u32LE(5),
			[0x2f], // signature byte
			u32LE(packed)
		);
		expect(decodeImageDimensions(webp, 'image/webp')).toEqual({ width, height });
	});
});

describe('decodeImageDimensions - WebP VP8 (lossy)', () => {
	it('reads the 14-bit dimension fields after the start code', () => {
		const webp = bytesFrom(
			[0x52, 0x49, 0x46, 0x46],
			u32LE(999),
			[0x57, 0x45, 0x42, 0x50],
			[0x56, 0x50, 0x38, 0x20], // 'VP8 '
			u32LE(10),
			[0x00, 0x00, 0x00], // frame tag
			[0x9d, 0x01, 0x2a], // start code
			u16LE(800), // width (top 2 bits are scale, unused here so 0)
			u16LE(600) // height
		);
		expect(decodeImageDimensions(webp, 'image/webp')).toEqual({ width: 800, height: 600 });
	});

	it('returns null when the start code does not match', () => {
		const webp = bytesFrom(
			[0x52, 0x49, 0x46, 0x46],
			u32LE(999),
			[0x57, 0x45, 0x42, 0x50],
			[0x56, 0x50, 0x38, 0x20],
			u32LE(10),
			[0x00, 0x00, 0x00],
			[0x00, 0x00, 0x00], // wrong start code
			u16LE(800),
			u16LE(600)
		);
		expect(decodeImageDimensions(webp, 'image/webp')).toBeNull();
	});
});

function u16BE(n: number): number[] {
	return [(n >>> 8) & 0xff, n & 0xff];
}

describe('decodeImageDimensions - JPEG', () => {
	it('reads height/width from the SOF0 marker', () => {
		const jpeg = bytesFrom(
			[0xff, 0xd8], // SOI
			[0xff, 0xe0], // APP0 marker
			[0x00, 0x02], // APP0 segment length (2 = no payload beyond the length field)
			[0xff, 0xc0], // SOF0 marker
			[0x00, 0x11], // SOF0 segment length (17) - not exact, unused past this point
			[0x08], // precision
			u16BE(480), // height
			u16BE(640), // width
			[0x00, 0x00] // trailing padding so the bounds check has room
		);
		expect(decodeImageDimensions(jpeg, 'image/jpeg')).toEqual({ width: 640, height: 480 });
	});

	it('returns null for a non-JPEG buffer', () => {
		expect(decodeImageDimensions(new Uint8Array([0, 1, 2, 3]), 'image/jpeg')).toBeNull();
	});
});

describe('decodeImageDimensions - unsupported mime type', () => {
	it('returns null for a type this module does not handle', () => {
		expect(decodeImageDimensions(new Uint8Array(30), 'image/gif')).toBeNull();
	});
});
