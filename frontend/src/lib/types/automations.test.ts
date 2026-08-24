import { describe, expect, it } from 'vitest';
import { parseDynamicPorts } from './automations';

describe('parseDynamicPorts', () => {
	it('parses comma-separated cases, trimming whitespace, and appends "default"', () => {
		expect(parseDynamicPorts('loras, checkpoints, vae')).toEqual([
			'loras',
			'checkpoints',
			'vae',
			'default'
		]);
	});

	it('drops blank entries', () => {
		expect(parseDynamicPorts('loras, , vae,')).toEqual(['loras', 'vae', 'default']);
	});

	it('drops duplicate entries, keeping first occurrence order', () => {
		expect(parseDynamicPorts('loras, vae, loras')).toEqual(['loras', 'vae', 'default']);
	});

	it('returns just "default" for an empty or blank string', () => {
		expect(parseDynamicPorts('')).toEqual(['default']);
		expect(parseDynamicPorts('   ')).toEqual(['default']);
	});

	it('returns just "default" for non-string input (undefined config value)', () => {
		expect(parseDynamicPorts(undefined)).toEqual(['default']);
		expect(parseDynamicPorts(null)).toEqual(['default']);
		expect(parseDynamicPorts(42)).toEqual(['default']);
	});
});
