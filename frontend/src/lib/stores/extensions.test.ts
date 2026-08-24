import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: { getClient: () => ({ get: mockGet }), setOnAuthExpired: vi.fn() }
}));

vi.mock('$lib/registries/artifactRendererRegistry', () => ({
	artifactRendererRegistry: { register: vi.fn() }
}));
vi.mock('$lib/registries/workbenchFileRendererRegistry', () => ({
	registerWorkbenchFileRenderer: vi.fn()
}));
vi.mock('$lib/registries/modelViewRegistry', () => ({
	registerModelView: vi.fn()
}));
vi.mock('$lib/generation/messages/pluginOutput', () => ({
	registerPluginOutputHandler: vi.fn()
}));

import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import { registerWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
import { registerModelView } from '$lib/registries/modelViewRegistry';
import { registerPluginOutputHandler } from '$lib/generation/messages/pluginOutput';
import { setContributions } from '$lib/extensions/extensionSlots';

vi.mock('$lib/extensions/extensionSlots', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/extensions/extensionSlots')>();
	return { ...actual, setContributions: vi.fn() };
});

// Import after mocks are set up; reset the module's `initialized` guard between tests via vi.resetModules.
async function loadInitExtensions() {
	vi.resetModules();
	const mod = await import('./extensions');
	return mod.initExtensions;
}

describe('stores/extensions', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('registers history.artifact renderers into artifactRendererRegistry', async () => {
		mockGet.mockResolvedValue({
			data: {
				success: true,
				data: {
					renderers: [
						{ plugin_id: 'example-extensions', kind: 'history.artifact', key: 'fake_artifact', component: 'FakeArtifact.js' }
					],
					contributions: []
				}
			}
		});

		const initExtensions = await loadInitExtensions();
		await initExtensions();

		expect(artifactRendererRegistry.register).toHaveBeenCalledWith('fake_artifact', {
			pluginId: 'example-extensions',
			asset: 'FakeArtifact.js'
		});
	});

	it('registers workbench.file, model.view, and generation.output renderers to their respective registries', async () => {
		mockGet.mockResolvedValue({
			data: {
				success: true,
				data: {
					renderers: [
						{ plugin_id: 'p1', kind: 'workbench.file', key: 'pdf', component: 'PdfPreview.js' },
						{ plugin_id: 'p2', kind: 'model.view', key: 'usage', component: 'UsageSection.js' },
						{ plugin_id: 'p3', kind: 'generation.output', key: 'custom_status', component: 'CustomStatus.js' }
					],
					contributions: []
				}
			}
		});

		const initExtensions = await loadInitExtensions();
		await initExtensions();

		expect(registerWorkbenchFileRenderer).toHaveBeenCalledWith('pdf', { pluginId: 'p1', asset: 'PdfPreview.js' });
		expect(registerModelView).toHaveBeenCalledWith('p2', 'usage', 'UsageSection.js');
		expect(registerPluginOutputHandler).toHaveBeenCalledWith('custom_status', 'p3', 'CustomStatus.js');
	});

	it('feeds contributions into extensionSlots', async () => {
		const contributions = [{ plugin_id: 'p1', slot: 'admin.tabs', component: 'Tab.js', order: 10 }];
		mockGet.mockResolvedValue({ data: { success: true, data: { renderers: [], contributions } } });

		const initExtensions = await loadInitExtensions();
		await initExtensions();

		expect(setContributions).toHaveBeenCalledWith(contributions);
	});

	it('is non-fatal when the request fails', async () => {
		mockGet.mockRejectedValue(new Error('network error'));

		const initExtensions = await loadInitExtensions();
		await expect(initExtensions()).resolves.toBeUndefined();
	});

	it('is non-fatal when the response is unsuccessful', async () => {
		mockGet.mockResolvedValue({ data: { success: false, message: 'nope' } });

		const initExtensions = await loadInitExtensions();
		await expect(initExtensions()).resolves.toBeUndefined();
		expect(artifactRendererRegistry.register).not.toHaveBeenCalled();
	});
});
