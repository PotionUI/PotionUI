/**
 * Width-only "chrome bucket" for justified-gallery tiles (`GenerationCard`'s
 * `tile` mode): as a tile grows, it progressively unlocks more per-card
 * affordances so the bottom info bar never has to truncate mid-string - a
 * bucket's lanes are always sized so its content fits with room to spare, and
 * lower-priority parts simply drop out at the next bucket down instead.
 * Thresholds and behaviour are the "reworked history cards" design contract
 * (`bucketFor` in the design mock) translated 1:1.
 */

export type ChromeBucketName = 'full' | 'regular' | 'compact' | 'micro' | 'nano';
export type ChromeStatusStyle = 'label' | 'dot';

export interface ChromeBucket {
	name: ChromeBucketName;
	/** Bottom info-bar height, px. */
	barHeight: number;
	showStars: boolean;
	showRatingChip: boolean;
	showBarTime: boolean;
	starSize: number;
	actionCount: number;
	checkboxSize: number;
	showHoverMeta: boolean;
	showMediaChip: boolean;
	statusStyle: ChromeStatusStyle;
	/** Below `micro` even a `W×H` pair won't fit - short side only (video convention). */
	resolutionShort: boolean;
}

const FULL: ChromeBucket = {
	name: 'full',
	barHeight: 30,
	showStars: true,
	showRatingChip: false,
	showBarTime: true,
	starSize: 13,
	actionCount: 4,
	checkboxSize: 20,
	showHoverMeta: true,
	showMediaChip: true,
	statusStyle: 'label',
	resolutionShort: false
};
const REGULAR: ChromeBucket = {
	name: 'regular',
	barHeight: 28,
	showStars: true,
	showRatingChip: false,
	showBarTime: false,
	starSize: 12,
	actionCount: 3,
	checkboxSize: 20,
	showHoverMeta: true,
	showMediaChip: true,
	statusStyle: 'label',
	resolutionShort: false
};
const COMPACT: ChromeBucket = {
	name: 'compact',
	barHeight: 24,
	showStars: false,
	showRatingChip: true,
	showBarTime: false,
	starSize: 11,
	actionCount: 2,
	checkboxSize: 18,
	showHoverMeta: false,
	showMediaChip: true,
	statusStyle: 'dot',
	resolutionShort: false
};
const MICRO: ChromeBucket = {
	name: 'micro',
	barHeight: 22,
	showStars: false,
	showRatingChip: false,
	showBarTime: false,
	starSize: 11,
	actionCount: 2,
	checkboxSize: 16,
	showHoverMeta: false,
	showMediaChip: false,
	statusStyle: 'dot',
	resolutionShort: false
};
const NANO: ChromeBucket = {
	name: 'nano',
	barHeight: 20,
	showStars: false,
	showRatingChip: false,
	showBarTime: false,
	starSize: 11,
	actionCount: 1,
	checkboxSize: 14,
	showHoverMeta: false,
	showMediaChip: false,
	statusStyle: 'dot',
	resolutionShort: true
};

/** Widest-first: the first bucket whose minimum width the tile satisfies wins. */
const THRESHOLDS: Array<[minWidth: number, bucket: ChromeBucket]> = [
	[300, FULL],
	[210, REGULAR],
	[140, COMPACT],
	[110, MICRO]
];

export function bucketForCardWidth(width: number): ChromeBucket {
	for (const [minWidth, bucket] of THRESHOLDS) {
		if (width >= minWidth) return bucket;
	}
	return NANO;
}

export type ChromeAction = 'favorite' | 'view' | 'download' | 'delete';

/**
 * Favorite and delete are the two actions that must survive down to the
 * smallest tile; view and download drop out first as the row runs out of room.
 */
export function actionsForCount(count: number): ChromeAction[] {
	if (count >= 4) return ['favorite', 'view', 'download', 'delete'];
	if (count === 3) return ['favorite', 'view', 'delete'];
	if (count === 2) return ['favorite', 'delete'];
	return ['delete'];
}

/** `W×H`, or (below `micro`) the short side alone in the `<n>p` video convention. */
export function formatBarResolution(width: number, height: number, short: boolean): string {
	return short ? `${Math.min(width, height)}p` : `${width}×${height}`;
}

/** A positive finite number - rejects `NaN`, `Infinity`, negatives, and non-numeric junk. */
function isUsableDimension(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

/**
 * File dimensions win when both halves of the pair are present (older rows and
 * best-effort video probing can leave one or both absent - `media_probe`'s
 * "None means not determined" contract, `src/features/media/media_probe.py`).
 * Falls back to the generation's own requested `form_data.width`/`height` so a
 * resolution still renders when the file record predates dimension capture;
 * renders nothing when neither source has a usable pair.
 */
export function resolveCardResolution(
	file: { width?: number | null; height?: number | null } | null | undefined,
	formData: Record<string, unknown> | null | undefined
): { width: number; height: number } | null {
	if (isUsableDimension(file?.width) && isUsableDimension(file?.height)) {
		return { width: file.width, height: file.height };
	}
	const formWidth = formData?.width;
	const formHeight = formData?.height;
	if (isUsableDimension(formWidth) && isUsableDimension(formHeight)) {
		return { width: formWidth, height: formHeight };
	}
	return null;
}

/**
 * The bottom-left media chip renders a video's duration only when it is the
 * sole file (a multi-file generation shows a `n/total` counter there
 * instead) - when it does, the bottom-right hover strip must drop duration
 * from its own parts so hovering a single-file video doesn't show the same
 * duration twice.
 */
export function mediaChipOwnsDuration(fileCount: number, isCurrentMediaVideo: boolean): boolean {
	return fileCount <= 1 && isCurrentMediaVideo;
}
