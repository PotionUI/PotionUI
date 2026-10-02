import { describe, it, expect } from 'vitest';
import { pickFromGeneration, pickFromUpload } from './mediaLoaderPicks';

describe('pickFromGeneration', () => {
	const file = {
		file_path: 'outputs/2025-10-10/01K77DF21Z2TH1YXS7NMTVR4CE/0.png',
		file_type: 'IMAGE',
		width: 1024,
		height: 1536,
		file_size: 2000
	};

	it('serves the file from its generation and keeps both path conventions', () => {
		const { item } = pickFromGeneration(file);
		expect(item).toMatchObject({
			path: file.file_path,
			relative_path: file.file_path,
			url: '/api/media/generations/01K77DF21Z2TH1YXS7NMTVR4CE/0.png',
			name: '0.png',
			type: 'image'
		});
		expect(item.metadata).toMatchObject({ width: 1024, height: 1536, size: 2000 });
	});

	it('hands the limit check the numbers the history row already knows', () => {
		const { candidate } = pickFromGeneration({ ...file, file_type: 'video', duration_seconds: 4 });
		expect(candidate).toMatchObject({ name: '0.png', kind: 'video', sizeBytes: 2000, durationSeconds: 4 });
	});

	it('reads the kind from the extension when the row declares none', () => {
		expect(pickFromGeneration({ file_path: 'outputs/d/01K/clip.mp4' }).kind).toBe('video');
	});
});

describe('pickFromUpload', () => {
	const upload = {
		filename: 'abc123.png',
		original_filename: ' harbour.png ',
		media_type: 'image',
		url: '/api/media/uploads/abc123.png',
		width: 640,
		height: 480,
		size: 900
	} as any;

	it('files the pick under uploads and labels it with the library name', () => {
		const { item } = pickFromUpload(upload);
		expect(item).toMatchObject({
			path: 'uploads/abc123.png',
			relative_path: 'uploads/abc123.png',
			url: '/api/media/uploads/abc123.png',
			name: 'harbour.png',
			label: 'harbour.png',
			type: 'image'
		});
	});

	it('never invents a label from the placeholder name', () => {
		const { item } = pickFromUpload({ ...upload, original_filename: '  ' });
		expect(item.name).toBe('Upload');
		expect(item).not.toHaveProperty('label');
	});
});
