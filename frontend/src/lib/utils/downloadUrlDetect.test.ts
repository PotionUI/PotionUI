import { describe, it, expect } from 'vitest';
import { detectUrl } from './downloadUrlDetect';

describe('detectUrl', () => {
	it('returns an empty detection for a blank URL', () => {
		expect(detectUrl('')).toEqual({ hostname: null, filename: null, provider: null });
	});

	it('returns an empty detection for an unparsable URL', () => {
		expect(detectUrl('not a url')).toEqual({ hostname: null, filename: null, provider: null });
	});

	it('rejects non-http(s) protocols', () => {
		expect(detectUrl('ftp://example.com/model.safetensors')).toEqual({
			hostname: null,
			filename: null,
			provider: null
		});
	});

	it('guesses the filename from the URL basename, decoded', () => {
		const result = detectUrl('https://example.com/path/juggernaut%20xl-v11.safetensors');
		expect(result.filename).toBe('juggernaut xl-v11.safetensors');
	});

	it('returns a null filename when the path has no segments', () => {
		const result = detectUrl('https://example.com/');
		expect(result.filename).toBeNull();
	});

	it('matches a provider via a known host hint', () => {
		const providers = [
			{ id: 'p1', name: 'CivitAI' },
			{ id: 'p2', name: 'HuggingFace' }
		];
		const result = detectUrl(
			'https://civitai.com/api/download/models/1015439?type=Model&format=SafeTensor',
			providers
		);
		expect(result.hostname).toBe('civitai.com');
		expect(result.provider).toEqual({ id: 'p1', name: 'CivitAI' });
	});

	it('matches HuggingFace via its host hint', () => {
		const providers = [{ id: 'p2', name: 'HuggingFace' }];
		const result = detectUrl('https://huggingface.co/org/model/resolve/main/model.gguf', providers);
		expect(result.provider).toEqual({ id: 'p2', name: 'HuggingFace' });
	});

	it('falls back to matching the first hostname label when no known hint applies', () => {
		const providers = [{ id: 'p3', name: 'Example Provider' }];
		const result = detectUrl('https://example.com/model.safetensors', providers);
		expect(result.provider).toEqual({ id: 'p3', name: 'Example Provider' });
	});

	it('returns a null provider when nothing matches', () => {
		const providers = [{ id: 'p1', name: 'CivitAI' }];
		const result = detectUrl('https://example.com/model.safetensors', providers);
		expect(result.provider).toBeNull();
	});

	it('strips a leading www. before matching', () => {
		const providers = [{ id: 'p1', name: 'CivitAI' }];
		const result = detectUrl('https://www.civitai.com/models/1', providers);
		expect(result.hostname).toBe('civitai.com');
		expect(result.provider).toEqual({ id: 'p1', name: 'CivitAI' });
	});
});
