// @vitest-environment jsdom
//
// Covers the hop the pure-function tests (fileType.test.ts,
// workbenchGallery.test.ts) cannot: that Workbench.svelte's own reactive
// wiring actually reaches those functions and routes a mesh gallery item to
// the fallback renderer instead of falling through to `displayImage`'s plain
// `<img>` branch. `displayImage` is set to the gallery item's URL for EVERY
// file type (see the comment above `isFallbackFileType` in Workbench.svelte),
// so a mesh renders a broken `<img src=".../1.glb">` unless the fallback gate
// wins - which is exactly the regression this mounts to catch.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

// MeshPreview dynamically imports the real `@google/model-viewer` package on
// mount. It defines a real custom element whose internal (lit-based) update
// cycle keeps scheduling itself against a real WebGL/XR context this test
// never provides, throwing unhandled rejections well after the assertions
// below have already run. This test only needs to prove Workbench routes a
// mesh item to MeshPreview instead of ImagePreview - not that the 3D viewer
// itself renders - so the module is stubbed to a harmless custom element.
vi.mock('@google/model-viewer', () => ({}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { createClassComponent } = await import('svelte/legacy');

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;

	target = document.createElement('div');
	document.body.appendChild(target);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

const MESH_URL = '/api/media/generations/gen-1/1.glb';

function mountWithMesh(fileType: string) {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: {
			currentGeneration: { status: 'completed' },
			isGenerating: false,
			workbenchIndex: 0,
			workbenchTotal: 1,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			batchMeshes: [
				{
					url: MESH_URL,
					originalUrl: MESH_URL,
					file_type: fileType,
					mesh_name: '1.glb',
					mesh_format: 'glb'
				}
			]
		}
	});
}

describe('Workbench routes a mesh gallery item away from the image branch', () => {
	it('does not render a broken <img> for an UPPERCASE MESH item', async () => {
		mountWithMesh('MESH');
		await settle();

		const brokenImg = target.querySelector<HTMLImageElement>(`img[src="${MESH_URL}"]`);
		expect(brokenImg).toBeNull();
	});

	it('resolves the fallback (mesh) renderer, not the image renderer, for an UPPERCASE MESH item', async () => {
		mountWithMesh('MESH');
		await settle();

		// MeshPreview's own markup (EmptyState/Alert/Spinner + optional
		// <model-viewer>) is what should occupy the display area instead - any
		// one of these appearing proves the fallback branch, not the plain
		// <img> branch, was taken.
		const meshMarkers = [
			target.querySelector('model-viewer'),
			Array.from(target.querySelectorAll('*')).find((el) =>
				(el.textContent || '').includes('No model')
			),
			Array.from(target.querySelectorAll('*')).find((el) =>
				(el.textContent || '').includes("Couldn't display this model")
			),
			Array.from(target.querySelectorAll('*')).find((el) =>
				(el.textContent || '').includes('Loading model')
			)
		].filter(Boolean);

		expect(meshMarkers.length).toBeGreaterThan(0);
	});

	it('also routes a lowercase mesh item the same way', async () => {
		mountWithMesh('mesh');
		await settle();

		const brokenImg = target.querySelector<HTMLImageElement>(`img[src="${MESH_URL}"]`);
		expect(brokenImg).toBeNull();
	});
});
