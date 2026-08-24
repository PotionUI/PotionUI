import { describe, it, expect } from 'vitest';
import { decodeDataUrl, dataUrlToFile } from './maskFile';

// "hi" — short enough to assert byte for byte.
const PAYLOAD = 'aGk=';

describe('decodeDataUrl', () => {
	it('reads the mime type and the bytes', () => {
		const { mimeType, bytes } = decodeDataUrl(`data:image/png;base64,${PAYLOAD}`);
		expect(mimeType).toBe('image/png');
		expect(Array.from(bytes)).toEqual([104, 105]);
	});

	it('reads the mime type off the header rather than assuming PNG', () => {
		expect(decodeDataUrl(`data:image/webp;base64,${PAYLOAD}`).mimeType).toBe('image/webp');
	});

	it('defaults the mime type when the header names none', () => {
		expect(decodeDataUrl(`data:;base64,${PAYLOAD}`).mimeType).toBe('image/png');
	});

	it('refuses anything that is not a base64 data URL', () => {
		expect(() => decodeDataUrl('/api/media/uploads/mask.png')).toThrow('could not be read');
		expect(() => decodeDataUrl('data:image/png,notbase64')).toThrow('could not be read');
		expect(() => decodeDataUrl('')).toThrow('could not be read');
	});
});

describe('dataUrlToFile', () => {
	it('names the file and carries its type', () => {
		const file = dataUrlToFile(`data:image/png;base64,${PAYLOAD}`, 'mask-1.png');
		expect(file.name).toBe('mask-1.png');
		expect(file.type).toBe('image/png');
		expect(file.size).toBe(2);
	});
});
