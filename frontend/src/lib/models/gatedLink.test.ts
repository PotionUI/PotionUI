import { describe, it, expect } from 'vitest';
import { gatedPageUrl } from './gatedLink';

describe('gatedPageUrl', () => {
	it('prefers license_url over any repo reference', () => {
		expect(
			gatedPageUrl({
				license_url: 'https://huggingface.co/Lightricks/LTX-2.5',
				provider: 'huggingface',
				ref: '{"repo": "other/repo"}'
			})
		).toBe('https://huggingface.co/Lightricks/LTX-2.5');
	});

	it('builds the page from a huggingface provider ref with a repo key', () => {
		expect(
			gatedPageUrl({
				provider: 'huggingface',
				ref: '{"repo": "Lightricks/LTX-2.5", "file": "a/b.safetensors", "revision": "main"}'
			})
		).toBe('https://huggingface.co/Lightricks/LTX-2.5');
	});

	it('accepts a bare repo id ref and the source and repo_id pair', () => {
		expect(gatedPageUrl({ provider: 'huggingface', ref: 'org/name' })).toBe('https://huggingface.co/org/name');
		expect(gatedPageUrl({ source: 'huggingface', repo_id: 'org/name' })).toBe('https://huggingface.co/org/name');
	});

	it('uses the failed download repo when no licence url is known', () => {
		expect(gatedPageUrl({ gated_repo: 'org/name' })).toBe('https://huggingface.co/org/name');
	});

	it('returns null for a malformed ref, a foreign provider or nothing at all', () => {
		expect(gatedPageUrl({ provider: 'huggingface', ref: '{"repo": ' })).toBeNull();
		expect(gatedPageUrl({ provider: 'huggingface', ref: '{"repo": "../etc"}' })).toBeNull();
		expect(gatedPageUrl({ provider: 'civitai', ref: '{"model_id": "123"}' })).toBeNull();
		expect(gatedPageUrl({ provider: 'huggingface', ref: null })).toBeNull();
		expect(gatedPageUrl(null)).toBeNull();
		expect(gatedPageUrl({})).toBeNull();
	});

	it('ignores a licence url that is not http(s)', () => {
		expect(gatedPageUrl({ license_url: 'javascript:alert(1)' })).toBeNull();
		expect(gatedPageUrl({ license_url: 'not a url', source: 'huggingface', repo_id: 'org/name' })).toBe(
			'https://huggingface.co/org/name'
		);
	});
});
