export type GenerationCardActionMode = 'full' | 'favorite' | 'none';
export type GenerationCardMetadataMode = 'none' | 'compact' | 'full';

export interface GenerationCardDensity {
	isLegacy: boolean;
	showMediaType: boolean;
	showStatus: boolean;
	actionMode: GenerationCardActionMode;
	metadataMode: GenerationCardMetadataMode;
	showMetadata: boolean;
	showSystemTags: boolean;
	showCarouselArrows: boolean;
	showCarouselCounter: boolean;
	showCaption: boolean;
	showCaptionTime: boolean;
}

const LEGACY_DENSITY: GenerationCardDensity = {
	isLegacy: true,
	showMediaType: true,
	showStatus: true,
	actionMode: 'full',
	metadataMode: 'full',
	showMetadata: true,
	showSystemTags: true,
	showCarouselArrows: true,
	showCarouselCounter: true,
	showCaption: true,
	showCaptionTime: true
};

/**
 * Overlay controls need both horizontal and vertical room: width alone is not
 * enough for a short justified panorama, and height alone is not enough for a
 * portrait sliver. A null tile is the legacy fixed-aspect card and deliberately
 * retains every existing affordance.
 */
export function getGenerationCardDensity(tile: { width: number; height: number } | null): GenerationCardDensity {
	if (!tile) return LEGACY_DENSITY;
	const { width, height } = tile;
	const mediaOverlayRoom = width >= 96 && height >= 64;

	return {
		isLegacy: false,
		showMediaType: mediaOverlayRoom,
		showStatus: mediaOverlayRoom,
		actionMode: width >= 176 && height >= 96 ? 'full' : width >= 96 && height >= 72 ? 'favorite' : 'none',
		metadataMode: width >= 300 && height >= 120 ? 'full' : width >= 180 && height >= 120 ? 'compact' : 'none',
		showMetadata: width >= 180 && height >= 120,
		showSystemTags: width >= 300 && height >= 120,
		showCarouselArrows: width >= 104 && height >= 96,
		showCarouselCounter: width >= 144 && height >= 128,
		showCaption: width >= 96,
		showCaptionTime: width >= 180
	};
}

/** A running/error status occupies the counter's bottom-center lane on justified cards. */
export function shouldShowGenerationCardCounter(density: GenerationCardDensity, status: string): boolean {
	return density.showCarouselCounter && (density.isLegacy || status === 'completed');
}

/** The metadata strip only ever occupies the completed, non-empty case the density table reserves room for. */
export function shouldShowGenerationCardMetadataStrip(
	density: GenerationCardDensity,
	hasContent: boolean,
	status: string
): boolean {
	return density.metadataMode !== 'none' && hasContent && status === 'completed';
}

/**
 * The `1/N` counter sat at a fixed `bottom-8` regardless of what else was on
 * the tile, colliding with the metadata strip on short cards. When the strip
 * is rendering, the counter moves above it; otherwise it drops to the same
 * bottom lane as the status badge.
 */
export function getGenerationCardCounterOffsetClass(metadataStripVisible: boolean): 'bottom-8' | 'bottom-2' {
	return metadataStripVisible ? 'bottom-8' : 'bottom-2';
}
