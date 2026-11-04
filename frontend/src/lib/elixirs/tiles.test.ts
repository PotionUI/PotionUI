import { expect, test } from 'vitest';
import { formatDuration, fullSource, stillSource, tileKey, tileKind, tileName, videoSource } from './tiles';

const image = { kind: 'history', generation_id: 'g1', file_id: 'f1', media_type: 'IMAGE', original_filename: '0.png', preview_url: '/api/media/files/f1?size=medium' };
const video = { kind: 'history', generation_id: 'g2', file_id: 'f2', media_type: 'video', original_filename: '0.mp4', preview_url: '/api/media/files/f2?size=medium' };
const upload = { kind: 'library', item_id: 'u1', media_type: 'video', original_filename: 'clip.mp4', preview_url: '/api/media/uploads/clip.mp4?size=medium' };

test('a tile knows its kind whatever case the server used', () => {
	expect(tileKind(image)).toBe('image');
	expect(tileKind(video)).toBe('video');
	expect(tileKind({ media_type: 'mesh' })).toBe('image');
	expect(tileKind(null)).toBe('image');
});

test('an image tile shows the small preview and opens the full file', () => {
	expect(stillSource(image)).toBe('/api/media/files/f1?size=medium');
	expect(fullSource(image)).toBe('/api/media/files/f1');
});

test('a video tile plays the real file, never the still thumbnail', () => {
	expect(stillSource(video)).toBe(null);
	expect(videoSource(video)).toBe('/api/media/files/f2#t=0.1');
	expect(videoSource(upload)).toBe('/api/media/uploads/clip.mp4#t=0.1');
	expect(fullSource({ preview_url: '/x?size=small&v=2' })).toBe('/x?v=2');
	expect(fullSource({ media_url: '/direct', preview_url: '/x?size=small' })).toBe('/direct');
	expect(videoSource({ media_type: 'video' })).toBe(null);
});

test('tiles are named and keyed by what they point at', () => {
	expect(tileKey(image)).toBe('history:g1:f1');
	expect(tileKey(upload)).toBe('library:u1');
	expect(tileName({ kind: 'library' })).toBe('Library file');
	expect(tileName({ kind: 'history' })).toBe('Generated file');
});

test('video lengths read like a player clock', () => {
	expect(formatDuration(5.2)).toBe('0:05');
	expect(formatDuration(65)).toBe('1:05');
	expect(formatDuration(3725)).toBe('1:02:05');
	expect(formatDuration(Number.NaN)).toBe('');
	expect(formatDuration(undefined)).toBe('');
});
