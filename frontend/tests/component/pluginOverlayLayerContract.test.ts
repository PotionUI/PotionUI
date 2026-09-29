// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('$app/environment', () => ({ browser: true }));

import { resetLayerStackForTests } from '$lib/actions/layerStack';
import { initHostApi } from '$lib/plugin-api/host';

const { default: BaseModal } = await import('$lib/components/modals/BaseModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

type LegacyInstance = { $destroy: () => void };
type LayersApi = { acquire: (tier: string) => number; release: (tier: string, z: number) => void };

const mounted: LegacyInstance[] = [];

function mountBaseModal(title: string): LegacyInstance {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: BaseModal as never,
		target,
		props: { isOpen: true, title }
	}) as LegacyInstance;
	mounted.push(instance);
	return instance;
}

function modalBackdropZIndices(): number[] {
	return Array.from(document.body.querySelectorAll('div[role="button"][aria-label="Close modal"]')).map((el) =>
		Number((el as HTMLElement).style.zIndex)
	);
}

function layersApi(): LayersApi {
	return (window as unknown as { __potionui: { layers: LayersApi } }).__potionui.layers;
}

function openImageModalPluginStyleOverlayViaHostLayersApi(): { overlay: HTMLElement; close: () => void } {
	const z = layersApi().acquire('overlay');
	const portalContainer = document.createElement('div');
	portalContainer.id = 'image-modal-portal';
	document.body.appendChild(portalContainer);
	portalContainer.innerHTML = `<div class="image-modal-overlay" style="position: fixed; z-index: ${z};"></div>`;
	const overlay = portalContainer.querySelector('.image-modal-overlay') as HTMLElement;
	return {
		overlay,
		close() {
			layersApi().release('overlay', z);
			portalContainer.remove();
		}
	};
}

afterEach(() => {
	for (const instance of mounted.splice(0)) instance.$destroy();
	document.body.innerHTML = '';
	resetLayerStackForTests();
});

describe('window.__potionui.layers plugin contract', () => {
	it('acquires an increasing z-index per call and reuses a released slot on the next acquire', () => {
		initHostApi();
		const layers = layersApi();

		const first = layers.acquire('overlay');
		const second = layers.acquire('overlay');
		expect(second).toBeGreaterThan(first);

		layers.release('overlay', second);
		expect(layers.acquire('overlay')).toBe(second);
	});
});

describe('plugin overlay layering against a core modal', () => {
	it('stacks a plugin overlay opened after a core modal above it, and drops back to the modal floor once the plugin closes', () => {
		initHostApi();
		mountBaseModal('Existing modal');
		const [modalZ] = modalBackdropZIndices();

		const plugin = openImageModalPluginStyleOverlayViaHostLayersApi();
		expect(Number(plugin.overlay.style.zIndex)).toBeGreaterThan(modalZ);

		plugin.close();

		mountBaseModal('Later modal');
		const laterZ = modalBackdropZIndices()[1];
		expect(laterZ).toBe(modalZ + 1);
	});
});
