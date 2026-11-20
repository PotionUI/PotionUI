export interface MediaSize {
	width: number;
	height: number;
}

export interface MediaSizeOption extends MediaSize {
	value: string;
	label: string;
	tier: string;
}

export const SOURCE_TIER = 'Source';
export const COMMON_TIER = 'Common sizes';
export const SCALE_TIER = 'Scale';

export const MIN_MEDIA_SIDE = 64;
export const SHORT_EDGE_TARGETS = [1080, 720, 540, 480, 360];
export const SCALE_PERCENTS = [75, 50, 33, 25];

export function evenFloor(value: number): number {
	return Math.max(2, Math.floor(value / 2) * 2);
}

export function evenRound(value: number): number {
	return Math.max(2, Math.round(value / 2) * 2);
}

export function scaleSize(source: MediaSize, factor: number): MediaSize {
	return { width: evenRound(source.width * factor), height: evenRound(source.height * factor) };
}

export function isKnownSize(size: MediaSize | null | undefined): size is MediaSize {
	return !!size && size.width > 0 && size.height > 0;
}

export function deriveMediaSizeOptions(source: MediaSize | null | undefined): MediaSizeOption[] {
	if (!isKnownSize(source)) return [];
	const options: MediaSizeOption[] = [];
	const seen = new Set<string>();
	const add = (size: MediaSize, label: string, tier: string) => {
		if (size.width < MIN_MEDIA_SIDE || size.height < MIN_MEDIA_SIDE) return;
		const value = `${size.width}x${size.height}`;
		if (seen.has(value)) return;
		seen.add(value);
		options.push({ ...size, value, label, tier });
	};
	add({ width: evenFloor(source.width), height: evenFloor(source.height) }, 'Original', SOURCE_TIER);
	const shortEdge = Math.min(source.width, source.height);
	for (const edge of SHORT_EDGE_TARGETS) {
		if (edge < shortEdge) add(scaleSize(source, edge / shortEdge), `${edge}p`, COMMON_TIER);
	}
	for (const percent of SCALE_PERCENTS) {
		add(scaleSize(source, percent / 100), `${percent}%`, SCALE_TIER);
	}
	return options;
}

export function preferredMediaSize(options: MediaSizeOption[]): MediaSizeOption | null {
	return (
		options.find((option) => option.tier === COMMON_TIER) ??
		options.find((option) => option.label === '50%') ??
		options[0] ??
		null
	);
}

export function matchingSide(source: MediaSize, side: 'width' | 'height', typed: string): string {
	const text = (typed ?? '').trim();
	if (!/^\d+$/.test(text)) return '';
	const value = parseInt(text, 10);
	if (value <= 0) return '';
	const other =
		side === 'width' ? (value * source.height) / source.width : (value * source.width) / source.height;
	return String(evenRound(other));
}

export type CustomSizeResult = { ok: true; size: MediaSize } | { ok: false; error: string };

export function parseCustomSize(
	source: MediaSize,
	rawWidth: string,
	rawHeight: string,
	requireEven = true
): CustomSizeResult {
	const widthText = (rawWidth ?? '').trim();
	const heightText = (rawHeight ?? '').trim();
	if (!widthText || !heightText) return { ok: false, error: 'Enter a width and a height.' };
	if (!/^\d+$/.test(widthText) || !/^\d+$/.test(heightText)) {
		return { ok: false, error: 'Width and height must be whole numbers.' };
	}
	const width = parseInt(widthText, 10);
	const height = parseInt(heightText, 10);
	if (width < MIN_MEDIA_SIDE || height < MIN_MEDIA_SIDE) {
		return { ok: false, error: `Each side must be at least ${MIN_MEDIA_SIDE}px.` };
	}
	if (width > source.width || height > source.height) {
		return { ok: false, error: `Larger than the original ${source.width}×${source.height}.` };
	}
	if (requireEven && (width % 2 !== 0 || height % 2 !== 0)) {
		return { ok: false, error: 'Width and height must be even numbers.' };
	}
	return { ok: true, size: { width, height } };
}

export function pixelShare(source: MediaSize, target: MediaSize): number {
	if (!isKnownSize(source)) return 0;
	return Math.round(((target.width * target.height) / (source.width * source.height)) * 100);
}
