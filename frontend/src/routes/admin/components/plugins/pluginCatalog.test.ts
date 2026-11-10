import { describe, it, expect, vi, beforeEach } from 'vitest';

const post = vi.hoisted(() => vi.fn());

vi.mock('$lib/services/api/index', () => ({ api: { getClient: () => ({ post }) } }));

import {
	catalogActionLabel,
	catalogStatusLabel,
	installFromCatalog,
	readPluginCatalog,
	rememberCatalogUrl,
	rememberedCatalogUrl,
	type PluginCatalogEntry
} from './pluginCatalog';

function entry(status: PluginCatalogEntry['status']): PluginCatalogEntry {
	return {
		id: 'demo',
		name: 'Demo',
		version: '1.2.0',
		description: '',
		license: '',
		requires_core: '0.1.0',
		archive: null,
		sha256: null,
		status,
		reason: '',
		installed_version: null
	};
}

beforeEach(() => {
	post.mockReset();
});

describe('catalog entries', () => {
	it('offer an install or an update and nothing for the other statuses', () => {
		expect(catalogActionLabel(entry('available'))).toBe('Install');
		expect(catalogActionLabel(entry('update'))).toBe('Update to 1.2.0');
		for (const status of ['installed', 'older', 'unreadable', 'bundled', 'conflict', 'incompatible', 'unreleased'] as const) {
			expect(catalogActionLabel(entry(status))).toBeNull();
		}
	});

	it('name every status', () => {
		expect(catalogStatusLabel('conflict')).toBe('Installed elsewhere');
		expect(catalogStatusLabel('bundled')).toBe('Ships with PotionUI');
		expect(catalogStatusLabel('older')).toBe('Older than installed');
		expect(catalogStatusLabel('unreadable')).toBe('Versions not comparable');
	});
});

describe('the catalog calls', () => {
	it('read a catalog and install by catalog URL and plugin id, without a client timeout', async () => {
		post.mockResolvedValue({ data: { success: true, data: { ok: true } } });

		await readPluginCatalog('https://example.com/plugins.json');
		await installFromCatalog('https://example.com/plugins.json', 'demo');

		expect(post.mock.calls).toEqual([
			['/api/plugins/catalog', { url: 'https://example.com/plugins.json' }, { timeout: 0 }],
			['/api/plugins/catalog/install', { url: 'https://example.com/plugins.json', plugin_id: 'demo' }, { timeout: 0 }]
		]);
	});

	it('carry the reason the server gives for a refusal', async () => {
		const refusal = Object.assign(new Error('Request failed with status code 400'), {
			response: { data: { detail: { error: 'plugin_catalog_refused', message: 'Only https URLs are allowed.' } } }
		});
		post.mockImplementation(async () => {
			throw refusal;
		});

		await expect(readPluginCatalog('http://example.com/plugins.json')).rejects.toThrow('Only https URLs are allowed.');
	});
});

describe('the remembered catalog URL', () => {
	it('is empty and stays harmless where the browser storage is missing', () => {
		expect(rememberedCatalogUrl()).toBe('');
		expect(() => rememberCatalogUrl('https://example.com/plugins.json')).not.toThrow();
	});
});
