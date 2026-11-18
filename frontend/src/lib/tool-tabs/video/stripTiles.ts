export interface TileLayout {
	tileWidth: number;
	count: number;
	pixelWidth: number;
	pixelHeight: number;
}

export const MAX_TILES = 64;
export const WIDTH_THRESHOLD = 0.12;

export function tileLayout(stripWidth: number, stripHeight: number, aspect: number, dpr = 1): TileLayout | null {
	if (!(stripWidth > 0) || !(stripHeight > 0)) return null;
	const ratio = aspect > 0 && Number.isFinite(aspect) ? aspect : 16 / 9;
	const scale = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
	const tileWidth = Math.max(1, Math.round(stripHeight * ratio));
	const count = Math.min(MAX_TILES, Math.max(1, Math.ceil(stripWidth / tileWidth)));
	return {
		tileWidth,
		count,
		pixelWidth: Math.max(1, Math.round(tileWidth * scale)),
		pixelHeight: Math.max(1, Math.round(stripHeight * scale))
	};
}

export function tileTimes(duration: number, stripWidth: number, layout: TileLayout): number[] {
	if (!(duration > 0) || !(stripWidth > 0)) return [];
	const last = Math.max(0, duration - 0.05);
	return Array.from({ length: layout.count }, (_, i) => {
		const left = i * layout.tileWidth;
		const visible = Math.min(layout.tileWidth, stripWidth - left);
		return Math.min(last, Math.max(0, ((left + visible / 2) / stripWidth) * duration));
	});
}

export function settleWidth(current: number, next: number, threshold = WIDTH_THRESHOLD): number {
	if (!(next > 0)) return current;
	if (!(current > 0)) return next;
	return Math.abs(next - current) / current > threshold ? next : current;
}

export function createFrameCache(limit = 8) {
	const sets = new Map<string, (string | null)[]>();
	return {
		key(url: string, layout: TileLayout, duration: number) {
			return `${url}|${layout.pixelWidth}x${layout.pixelHeight}|${layout.count}|${duration.toFixed(3)}`;
		},
		get(key: string, count: number): (string | null)[] {
			const found = sets.get(key);
			if (found) {
				sets.delete(key);
				sets.set(key, found);
				return found.slice();
			}
			const fresh: (string | null)[] = Array(count).fill(null);
			sets.set(key, fresh);
			while (sets.size > limit) sets.delete(sets.keys().next().value as string);
			return fresh.slice();
		},
		set(key: string, index: number, value: string) {
			const found = sets.get(key);
			if (found && index >= 0 && index < found.length) found[index] = value;
		}
	};
}

export const frameCache = createFrameCache();
