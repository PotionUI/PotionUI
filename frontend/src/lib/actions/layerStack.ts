export type LayerTier = 'overlay' | 'toast' | 'tooltip';

const VAR_NAMES: Record<LayerTier, string> = {
	overlay: '--z-overlay',
	toast: '--z-toast',
	tooltip: '--z-tooltip'
};

const FALLBACK_FLOORS: Record<LayerTier, number> = {
	overlay: 1000,
	toast: 10000,
	tooltip: 11000
};

let cachedFloors: Record<LayerTier, number> | null = null;

function readFloors(): Record<LayerTier, number> {
	if (cachedFloors) return cachedFloors;
	if (typeof getComputedStyle !== 'function' || typeof document === 'undefined') {
		return FALLBACK_FLOORS;
	}
	const styles = getComputedStyle(document.documentElement);
	const floors = {} as Record<LayerTier, number>;
	for (const tier of Object.keys(VAR_NAMES) as LayerTier[]) {
		const raw = styles.getPropertyValue(VAR_NAMES[tier]).trim();
		const parsed = Number(raw);
		floors[tier] = Number.isFinite(parsed) && parsed > 0 ? parsed : FALLBACK_FLOORS[tier];
	}
	cachedFloors = floors;
	return floors;
}

const openLayers: Record<LayerTier, number[]> = {
	overlay: [],
	toast: [],
	tooltip: []
};

export function acquireLayer(tier: LayerTier = 'overlay'): number {
	const floor = readFloors()[tier];
	const open = openLayers[tier];
	const z = open.length ? Math.max(...open) + 1 : floor + 1;
	open.push(z);
	return z;
}

export function releaseLayer(tier: LayerTier, z: number): void {
	const open = openLayers[tier];
	const idx = open.indexOf(z);
	if (idx !== -1) open.splice(idx, 1);
}

export function resetLayerStackForTests(): void {
	for (const tier of Object.keys(openLayers) as LayerTier[]) {
		openLayers[tier] = [];
	}
	cachedFloors = null;
}
