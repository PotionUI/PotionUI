import { browser } from '$app/environment';
import { acquireLayer, releaseLayer, type LayerTier } from './layerStack';

export default function overlayLayer(node: HTMLElement, tier: LayerTier = 'overlay') {
	if (!browser) {
		return {
			destroy() {}
		};
	}

	const z = acquireLayer(tier);
	const zValue = String(z);
	node.style.zIndex = zValue;

	const observer = new MutationObserver(() => {
		if (node.style.zIndex !== zValue) {
			node.style.zIndex = zValue;
		}
	});
	observer.observe(node, { attributes: true, attributeFilter: ['style'] });

	return {
		destroy() {
			observer.disconnect();
			releaseLayer(tier, z);
		}
	};
}
