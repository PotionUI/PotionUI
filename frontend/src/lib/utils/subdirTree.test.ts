import { describe, it, expect } from 'vitest';
import { buildSubdirNodes } from './subdirTree';

describe('buildSubdirNodes', () => {
	it('returns an empty list for no subdirectories', () => {
		expect(buildSubdirNodes([])).toEqual([]);
	});

	it('derives depth, leaf and parent label for a top-level path', () => {
		expect(buildSubdirNodes(['sdxl'])).toEqual([
			{ path: 'sdxl', depth: 0, leaf: 'sdxl', parentLabel: '' }
		]);
	});

	it('derives depth, leaf and parent label for a nested path', () => {
		expect(buildSubdirNodes(['sdxl/characters'])).toEqual([
			{ path: 'sdxl/characters', depth: 1, leaf: 'characters', parentLabel: 'sdxl' }
		]);
	});

	it('handles multiple levels of nesting', () => {
		expect(buildSubdirNodes(['sdxl/characters/female'])).toEqual([
			{
				path: 'sdxl/characters/female',
				depth: 2,
				leaf: 'female',
				parentLabel: 'sdxl / characters'
			}
		]);
	});

	it('preserves input order (callers already sort parents before children)', () => {
		const nodes = buildSubdirNodes(['sdxl', 'sdxl/characters', 'flux']);
		expect(nodes.map((n) => n.path)).toEqual(['sdxl', 'sdxl/characters', 'flux']);
	});
});
