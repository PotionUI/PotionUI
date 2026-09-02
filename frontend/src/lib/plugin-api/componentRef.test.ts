import { describe, it, expect } from 'vitest';
import { parseComponentRef } from './componentRef';

describe('parseComponentRef', () => {
	it('splits a plugin component reference into plugin id and asset', () => {
		expect(parseComponentRef('plugin:civitai:ImportModal.svelte')).toEqual({
			pluginId: 'civitai',
			asset: 'ImportModal.svelte'
		});
	});

	it('keeps colons inside the asset path', () => {
		expect(parseComponentRef('plugin:x:dist:Modal.js')).toEqual({ pluginId: 'x', asset: 'dist:Modal.js' });
	});

	it('returns null for anything that is not a plugin reference', () => {
		expect(parseComponentRef(null)).toBeNull();
		expect(parseComponentRef('')).toBeNull();
		expect(parseComponentRef('core:Modal.svelte')).toBeNull();
		expect(parseComponentRef('plugin:x')).toBeNull();
		expect(parseComponentRef('plugin::Modal.svelte')).toBeNull();
	});
});
