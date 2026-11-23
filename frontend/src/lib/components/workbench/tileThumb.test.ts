import { describe, expect, it } from 'vitest';
import { occurrenceKeys, tileThumbUrl } from './tileThumb';

describe('occurrenceKeys', () => {
	it('numbers repeated urls by occurrence so keys stay unique and stable', () => {
		expect(occurrenceKeys(['a', 'b', 'a', 'a'])).toEqual(['a#0', 'b#0', 'a#1', 'a#2']);
		expect(occurrenceKeys(['b', 'a', 'a'])).toEqual(['b#0', 'a#0', 'a#1']);
	});
});

describe('tileThumbUrl', () => {
	it('asks the media API for a medium rendition of still generation and file images', () => {
		expect(tileThumbUrl('/api/media/generations/g1/a.png')).toBe('/api/media/generations/g1/a.png?size=medium');
		expect(tileThumbUrl('/api/media/generations/g1/a.JPG?v=1')).toBe('/api/media/generations/g1/a.JPG?v=1&size=medium');
	});

	it('keeps a fragment at the end', () => {
		expect(tileThumbUrl('/api/media/generations/g1/a.png#x')).toBe('/api/media/generations/g1/a.png?size=medium#x');
		expect(tileThumbUrl('/api/media/generations/g1/a.png?v=1#x')).toBe('/api/media/generations/g1/a.png?v=1&size=medium#x');
	});

	it('leaves animated, extensionless, sized, foreign, data and blob urls alone', () => {
		expect(tileThumbUrl('/api/media/generations/g1/a.gif')).toBe('/api/media/generations/g1/a.gif');
		expect(tileThumbUrl('/api/media/generations/g1/a.webp')).toBe('/api/media/generations/g1/a.webp');
		expect(tileThumbUrl('/api/media/files/42')).toBe('/api/media/files/42');
		expect(tileThumbUrl('/api/media/generations/g1/a.png?size=large')).toBe('/api/media/generations/g1/a.png?size=large');
		expect(tileThumbUrl('https://cdn.example/api/media/generations/g1/a.png')).toBe('https://cdn.example/api/media/generations/g1/a.png');
		expect(tileThumbUrl('/other/api/media/generations/g1/a.png')).toBe('/other/api/media/generations/g1/a.png');
		expect(tileThumbUrl('data:image/png;base64,AAAA')).toBe('data:image/png;base64,AAAA');
		expect(tileThumbUrl('blob:x/1')).toBe('blob:x/1');
		expect(tileThumbUrl(null)).toBe('');
	});
});
