import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: mockGet }),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn()
	}
}));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

// A `generation.output` disposer is a real teardown: it also drops the output
// messages already stored on every tab. Spying on it is the direct way to see
// which registrations a refresh treats as retained and which as removed.
const registerOutput = vi.fn();
const unregisterOutput = vi.fn();
vi.mock('$lib/generation/messages/pluginOutput', () => ({
	registerPluginOutputHandler: (...args: unknown[]) => registerOutput(...args),
	unregisterPluginOutputHandler: (...args: unknown[]) => unregisterOutput(...args)
}));

type Snapshot = { renderers?: any[]; revisions?: Record<string, string> };

function respondWith(snapshot: Snapshot) {
	const bodies: Record<string, unknown> = {
		'/api/plugins/frontend-extensions': {
			renderers: snapshot.renderers ?? [],
			contributions: [],
			revisions: snapshot.revisions ?? {}
		},
		'/api/fields/types': [],
		'/api/plugins/pages': [],
		'/api/plugins/quick-actions': [],
		'/api/plugins/sidebar-widgets': [],
		'/api/plugins/hooks/frontend': {}
	};
	mockGet.mockImplementation(async (path: string) => ({
		data: { success: true, data: bodies[path] }
	}));
}

async function loadRefresh() {
	vi.resetModules();
	return import('./extensionRefresh');
}

const OUTPUT_A = {
	plugin_id: 'a',
	kind: 'generation.output',
	key: 'a_output',
	component: 'AOutput.js'
};
const OUTPUT_B = {
	plugin_id: 'b',
	kind: 'generation.output',
	key: 'b_output',
	component: 'BOutput.js'
};

describe('applySnapshot reconciliation', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('retains an unchanged registration across an identical refresh', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });

		await refreshPluginExtensions();
		unregisterOutput.mockClear();
		await refreshPluginExtensions();

		expect(unregisterOutput).not.toHaveBeenCalled();
	});

	it('retains one plugin’s registration while another is enabled alongside it', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		respondWith({ renderers: [OUTPUT_A, OUTPUT_B], revisions: { a: 'rev-1', b: 'rev-1' } });
		await refreshPluginExtensions();

		expect(unregisterOutput).not.toHaveBeenCalled();
	});

	it('retains one plugin’s registration while another is disabled alongside it', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A, OUTPUT_B], revisions: { a: 'rev-1', b: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		expect(unregisterOutput.mock.calls).toEqual([['b_output', 'b']]);
	});

	it('disposes a registration whose plugin is gone', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		respondWith({});
		await refreshPluginExtensions();

		expect(unregisterOutput.mock.calls).toEqual([['a_output', 'a']]);
	});

	it('disposes and re-registers a registration whose revision moved', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		registerOutput.mockClear();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-2' } });
		await refreshPluginExtensions();

		expect(unregisterOutput.mock.calls).toEqual([['a_output', 'a']]);
		expect(registerOutput.mock.calls).toEqual([['a_output', 'a', 'AOutput.js']]);
	});

	it('disposes a registration whose declared component changed', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		respondWith({
			renderers: [{ ...OUTPUT_A, component: 'AOutput.v2.js' }],
			revisions: { a: 'rev-1' }
		});
		await refreshPluginExtensions();

		expect(unregisterOutput.mock.calls).toEqual([['a_output', 'a']]);
	});

	it('disposes nothing when the refresh fails', async () => {
		const { refreshPluginExtensions } = await loadRefresh();
		respondWith({ renderers: [OUTPUT_A], revisions: { a: 'rev-1' } });
		await refreshPluginExtensions();

		unregisterOutput.mockClear();
		mockGet.mockRejectedValue(new Error('network error'));
		await refreshPluginExtensions();

		expect(unregisterOutput).not.toHaveBeenCalled();
	});
});
