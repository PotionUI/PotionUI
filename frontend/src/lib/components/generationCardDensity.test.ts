import { describe, expect, it } from 'vitest';
import {
	getGenerationCardDensity,
	shouldShowGenerationCardCounter,
	shouldShowGenerationCardMetadataStrip,
	getGenerationCardCounterOffsetClass
} from './generationCardDensity';

describe('getGenerationCardDensity', () => {
	it('keeps every legacy-card affordance', () => {
		expect(getGenerationCardDensity(null)).toEqual({
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
		});
	});

	it('removes optional controls from an extremely narrow portrait', () => {
		expect(getGenerationCardDensity({ width: 80, height: 300 })).toMatchObject({
			showMediaType: false,
			showStatus: false,
			actionMode: 'none',
			metadataMode: 'none',
			showMetadata: false,
			showSystemTags: false,
			showCarouselArrows: false,
			showCarouselCounter: false,
			showCaption: false,
			showCaptionTime: false
		});
	});

	it('uses a favorite-only compact square with no crowded caption time', () => {
		expect(getGenerationCardDensity({ width: 120, height: 120 })).toMatchObject({
			showMediaType: true,
			showStatus: true,
			actionMode: 'favorite',
			metadataMode: 'none',
			showMetadata: false,
			showCarouselArrows: true,
			showCarouselCounter: false,
			showCaption: true,
			showCaptionTime: false
		});
	});

	it('retains normal-tile controls while reserving system tags for wide cards', () => {
		expect(getGenerationCardDensity({ width: 240, height: 160 })).toMatchObject({
			actionMode: 'full',
			metadataMode: 'compact',
			showMetadata: true,
			showSystemTags: false,
			showCarouselArrows: true,
			showCarouselCounter: true,
			showCaptionTime: true
		});
	});

	it('keeps a roomy justified tile fully detailed', () => {
		expect(getGenerationCardDensity({ width: 300, height: 128 })).toMatchObject({
			showMediaType: true,
			showStatus: true,
			actionMode: 'full',
			metadataMode: 'full',
			showSystemTags: true,
			showCarouselArrows: true,
			showCarouselCounter: true,
			showCaption: true,
			showCaptionTime: true
		});
	});

	it('degrades a wide but short panorama by height as well as width', () => {
		expect(getGenerationCardDensity({ width: 360, height: 80 })).toMatchObject({
			showMediaType: true,
			showStatus: true,
			actionMode: 'favorite',
			metadataMode: 'none',
			showMetadata: false,
			showSystemTags: false,
			showCarouselArrows: false,
			showCarouselCounter: false,
			showCaption: true,
			showCaptionTime: true
		});
	});

	it('honors inclusive control boundaries', () => {
		expect(getGenerationCardDensity({ width: 95, height: 64 }).showMediaType).toBe(false);
		expect(getGenerationCardDensity({ width: 96, height: 64 }).showMediaType).toBe(true);
		expect(getGenerationCardDensity({ width: 175, height: 96 }).actionMode).toBe('favorite');
		expect(getGenerationCardDensity({ width: 176, height: 96 }).actionMode).toBe('full');
		expect(getGenerationCardDensity({ width: 299, height: 120 }).metadataMode).toBe('compact');
		expect(getGenerationCardDensity({ width: 300, height: 120 }).metadataMode).toBe('full');
		expect(getGenerationCardDensity({ width: 179, height: 120 }).showCaptionTime).toBe(false);
		expect(getGenerationCardDensity({ width: 180, height: 120 }).showCaptionTime).toBe(true);
	});

	it('reserves the carousel counter lane for non-completed justified cards only', () => {
		const justifiedDensity = getGenerationCardDensity({ width: 240, height: 160 });
		expect(justifiedDensity.isLegacy).toBe(false);
		expect(shouldShowGenerationCardCounter(justifiedDensity, 'completed')).toBe(true);
		expect(shouldShowGenerationCardCounter(justifiedDensity, 'running')).toBe(false);
		expect(shouldShowGenerationCardCounter(justifiedDensity, 'failed')).toBe(false);

		const legacyDensity = getGenerationCardDensity(null);
		expect(shouldShowGenerationCardCounter(legacyDensity, 'running')).toBe(true);
		expect(shouldShowGenerationCardCounter(legacyDensity, 'failed')).toBe(true);
	});
});

describe('shouldShowGenerationCardMetadataStrip', () => {
	it('requires room, content, and a completed status all at once', () => {
		const roomy = getGenerationCardDensity({ width: 300, height: 128 });
		expect(shouldShowGenerationCardMetadataStrip(roomy, true, 'completed')).toBe(true);
		expect(shouldShowGenerationCardMetadataStrip(roomy, false, 'completed')).toBe(false);
		expect(shouldShowGenerationCardMetadataStrip(roomy, true, 'running')).toBe(false);

		const cramped = getGenerationCardDensity({ width: 120, height: 120 });
		expect(shouldShowGenerationCardMetadataStrip(cramped, true, 'completed')).toBe(false);
	});
});

describe('getGenerationCardCounterOffsetClass', () => {
	it('clears the metadata strip when both render, otherwise sits in the badge lane', () => {
		expect(getGenerationCardCounterOffsetClass(true)).toBe('bottom-8');
		expect(getGenerationCardCounterOffsetClass(false)).toBe('bottom-2');
	});
});
