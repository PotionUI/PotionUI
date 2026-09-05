import { describe, it, expect, vi, beforeEach } from 'vitest';

const errors: unknown[][] = [];
vi.mock('$lib/utils/logger', () => ({
	logger: {
		error: (...args: unknown[]) => errors.push(args),
		warn: () => {},
		info: () => {},
		debug: () => {}
	}
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getBaseURL: () => 'https://potion.test',
		getToken: () => null,
		setOnAuthExpired: vi.fn()
	}
}));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

/** The module URLs the resolver actually attempted for `asset`, in order. */
function attemptedUrls(asset: string): string[] {
	return errors
		.map((args) => String(args[0]))
		.filter((message) => message.includes('Failed to import component module'))
		.map((message) => (message.split(' ').pop() as string).replace(/:$/, ''))
		.filter((url) => url.split('?')[0].endsWith(`/${asset}`));
}

async function loadResolver() {
	vi.resetModules();
	errors.length = 0;
	return import('./componentResolver');
}

describe('resolvePluginComponent revisions', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('imports a plugin asset at the URL its current revision names', async () => {
		const resolver = await loadResolver();

		resolver.setPluginRevisions({ example: 'rev-1' });
		await resolver.resolvePluginComponent('example', 'Versioned.svelte');

		resolver.setPluginRevisions({ example: 'rev-2' });
		await resolver.resolvePluginComponent('example', 'Versioned.svelte');

		expect(attemptedUrls('Versioned.js')).toEqual([
			'https://potion.test/api/plugins/example/assets/Versioned.js?v=rev-1',
			'https://potion.test/api/plugins/example/assets/Versioned.js?v=rev-2'
		]);
	});

	it('shares one in-flight import between concurrent callers', async () => {
		const resolver = await loadResolver();
		resolver.setPluginRevisions({ example: 'rev-1' });

		const first = resolver.resolvePluginComponent('example', 'Cached.svelte');
		const second = resolver.resolvePluginComponent('example', 'Cached.svelte');
		await Promise.all([first, second]);

		expect(second).toBe(first);
		expect(attemptedUrls('Cached.js')).toHaveLength(1);
	});

	it('starts a fresh import after a revision eviction rather than reusing the evicted load', async () => {
		const resolver = await loadResolver();
		resolver.setPluginRevisions({ example: 'rev-1' });

		const evicted = resolver.resolvePluginComponent('example', 'Evicted.svelte');
		resolver.setPluginRevisions({ example: 'rev-2' });
		await evicted;

		const reloaded = resolver.resolvePluginComponent('example', 'Evicted.svelte');
		await reloaded;

		expect(reloaded).not.toBe(evicted);
		expect(attemptedUrls('Evicted.js')).toEqual([
			'https://potion.test/api/plugins/example/assets/Evicted.js?v=rev-1',
			'https://potion.test/api/plugins/example/assets/Evicted.js?v=rev-2'
		]);
	});

	it('retries a failed import instead of caching the failure', async () => {
		const resolver = await loadResolver();
		resolver.setPluginRevisions({ example: 'rev-1' });

		expect(await resolver.resolvePluginComponent('example', 'Retried.svelte')).toBeNull();
		expect(await resolver.resolvePluginComponent('example', 'Retried.svelte')).toBeNull();

		expect(attemptedUrls('Retried.js')).toHaveLength(2);
	});
});
