import { describe, it, expect } from 'vitest';
import { readSingleValue } from './mediaLoaderValue';

describe('readSingleValue', () => {
	it('reads an item object', () => {
		const value = {
			url: '/api/media/uploads/a.png',
			name: 'a.png',
			type: 'image',
			metadata: { width: 10, height: 20 }
		};
		expect(readSingleValue(value)).toEqual({
			previewUrl: '/api/media/uploads/a.png',
			fileName: 'a.png',
			fileType: 'image',
			metadata: { width: 10, height: 20 }
		});
	});

	it('takes the kind from the name when the object declares none', () => {
		expect(readSingleValue({ url: '/x', name: 'clip.mp4' })?.fileType).toBe('video');
	});

	it('resolves a legacy path string to its served address', () => {
		expect(readSingleValue('uploads/cat.png')).toEqual({
			previewUrl: '/api/media/uploads/cat.png',
			fileName: 'cat.png',
			fileType: 'image',
			metadata: null
		});
	});

	it.each([
		['C:\\x\\tmp\\a.png', 'a.png'],
		['C:\\x\\storage\\uploads\\cat.png', 'cat.png'],
		['/tmp/potionui/storage/uploads/clip.mp4', 'clip.mp4']
	])('names the file of %s without its folders', (path, name) => {
		expect(readSingleValue(path)?.fileName).toBe(name);
	});

	it('reads nothing from an empty value', () => {
		expect(readSingleValue(null)).toBeNull();
		expect(readSingleValue('')).toBeNull();
		expect(readSingleValue(undefined)).toBeNull();
	});
});
