import { describe, it, expect } from 'vitest';
import { kindFromDeclared, kindFromFilename, kindFromMimeType, kindOfMediaItem } from './mediaLoaderKind';

describe('kindFromMimeType', () => {
	it('reads the MIME family', () => {
		expect(kindFromMimeType('image/webp')).toBe('image');
		expect(kindFromMimeType('video/quicktime')).toBe('video');
		expect(kindFromMimeType('audio/flac')).toBe('audio');
	});

	it('returns null for anything else', () => {
		expect(kindFromMimeType('application/pdf')).toBeNull();
		expect(kindFromMimeType(undefined)).toBeNull();
	});
});

describe('kindFromFilename', () => {
	it('reads the extension, case-insensitively', () => {
		expect(kindFromFilename('SHOT.PNG')).toBe('image');
		expect(kindFromFilename('clip.mov')).toBe('video');
		expect(kindFromFilename('vo_take_03.wav')).toBe('audio');
	});

	it('ignores a query string on a served url', () => {
		expect(kindFromFilename('/api/media/uploads/a.mp4?v=2')).toBe('video');
	});

	it('returns null for an unknown extension', () => {
		expect(kindFromFilename('model.glb')).toBeNull();
	});
});

describe('kindFromDeclared', () => {
	// History rows serialize `file_type` uppercase; the generation WebSocket
	// types everything lowercase.
	it('accepts either casing', () => {
		expect(kindFromDeclared('IMAGE')).toBe('image');
		expect(kindFromDeclared('video')).toBe('video');
	});

	it('accepts a MIME type in the same slot', () => {
		expect(kindFromDeclared('audio/mpeg')).toBe('audio');
	});

	it('returns null for a kind this field does not handle', () => {
		expect(kindFromDeclared('MESH')).toBeNull();
	});
});

describe('kindOfMediaItem', () => {
	it('prefers the declared type', () => {
		expect(kindOfMediaItem({ type: 'video', name: 'thumb.png' })).toBe('video');
	});

	it('falls back to the name, then to either path convention', () => {
		expect(kindOfMediaItem({ name: 'shot.png' })).toBe('image');
		expect(kindOfMediaItem({ relative_path: 'generations/2026-01-01/abc/0.mp4' })).toBe('video');
		expect(kindOfMediaItem({ path: 'storage/uploads/take.wav' })).toBe('audio');
	});

	it('reads a bare path string', () => {
		expect(kindOfMediaItem('uploads/a.webp')).toBe('image');
	});

	it('returns null when nothing identifies the item', () => {
		expect(kindOfMediaItem({ label: 'Hero' })).toBeNull();
		expect(kindOfMediaItem(null)).toBeNull();
	});
});
