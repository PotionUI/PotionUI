import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
}));

import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import {
	registerWorkbenchFileRenderer,
	unregisterWorkbenchFileRenderer,
	resolveWorkbenchFileRenderer,
	hasWorkbenchFileRenderer
} from './workbenchFileRendererRegistry';

// One shared reference registered as the "image" core default in `beforeEach` -
// the registry caches resolved components by key, so reusing the same object
// identity across tests (rather than a fresh literal per test) keeps `toBe`
// assertions meaningful regardless of cache staleness between tests.
const ImagePreview = { name: 'ImagePreview' };

describe('workbenchFileRendererRegistry', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		registerWorkbenchFileRenderer('image', { component: ImagePreview });
	});

	it('resolves a statically registered core renderer', async () => {
		await expect(resolveWorkbenchFileRenderer('image')).resolves.toBe(ImagePreview);
	});

	it('falls back to the image renderer for an unregistered file_type', async () => {
		await expect(resolveWorkbenchFileRenderer('totally_unknown_type')).resolves.toBe(ImagePreview);
	});

	it('resolves a lazy plugin entry for a new file_type', async () => {
		const PdfPreview = { name: 'PdfPreview' };
		vi.mocked(resolvePluginComponent).mockResolvedValue(PdfPreview);
		registerWorkbenchFileRenderer('pdf', { pluginId: 'example-extensions', asset: 'PdfPreview.js' });

		await expect(resolveWorkbenchFileRenderer('pdf')).resolves.toBe(PdfPreview);
		expect(resolvePluginComponent).toHaveBeenCalledWith('example-extensions', 'PdfPreview.js');
	});

	it('unregister removes the entry, falling back to image again', async () => {
		registerWorkbenchFileRenderer('pdf', { component: { name: 'PdfPreview' } });
		expect(hasWorkbenchFileRenderer('pdf')).toBe(true);

		unregisterWorkbenchFileRenderer('pdf');

		expect(hasWorkbenchFileRenderer('pdf')).toBe(false);
		await expect(resolveWorkbenchFileRenderer('pdf')).resolves.toBe(ImagePreview);
	});
});
