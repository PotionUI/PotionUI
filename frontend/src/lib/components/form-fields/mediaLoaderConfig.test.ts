import { describe, it, expect } from 'vitest';
import {
	acceptAttribute,
	describeDropTarget,
	describeFormats,
	describeKinds,
	readAcceptedKinds,
	readMediaLoaderConfig
} from './mediaLoaderConfig';

describe('readAcceptedKinds', () => {
	it('reads a list of kind names', () => {
		expect(readAcceptedKinds({ accepted_types: ['video', 'image'] })).toEqual(['image', 'video']);
	});

	it('reads the legacy comma-separated MIME string', () => {
		expect(readAcceptedKinds({ accept: 'image/png,image/webp' })).toEqual(['image']);
		expect(readAcceptedKinds({ accept: 'image/*,video/*' })).toEqual(['image', 'video']);
	});

	it('reads a list of MIME globs', () => {
		expect(readAcceptedKinds({ accepted_types: ['audio/*'] })).toEqual(['audio']);
	});

	it('falls back to images and videos when the field declares nothing', () => {
		expect(readAcceptedKinds({})).toEqual(['image', 'video']);
	});

	it('ignores tokens that name no kind rather than widening to the default', () => {
		expect(readAcceptedKinds({ accepted_types: ['image', 'model/gltf-binary'] })).toEqual(['image']);
	});

	// The regression this module exists for: `accept` is emitted at the top
	// level while `allow_inpaint` is nested, so a reader that switches to
	// `config.configuration` wholesale loses the restriction.
	it('finds accept at the top level even when a nested configuration exists', () => {
		const config = { accept: 'image/*', configuration: { allow_inpaint: true } };
		expect(readAcceptedKinds(config)).toEqual(['image']);
	});

	it('finds accepted types nested under configuration when the top level has none', () => {
		expect(readAcceptedKinds({ configuration: { accepted_types: ['audio'] } })).toEqual(['audio']);
	});
});

describe('readMediaLoaderConfig', () => {
	// The keys and depths a real field emits: `accepted_types`, `max_items`
	// and the `max_*` constraint keys are echoed at the TOP level by
	// `media_input.echo_configured_constraints`, `allow_inpaint` is nested
	// under `configuration`, and the older size/axis caps live under
	// `validation` in camelCase.
	it('reads the constraint keys a media field emits', () => {
		const limits = readMediaLoaderConfig({
			accept: 'image/*,video/*',
			accepted_types: ['image', 'video'],
			multiple: true,
			max_items: 4,
			max_resolution: 2048,
			max_video_duration_seconds: 12,
			max_total_video_duration_seconds: 30,
			max_audio_duration_seconds: 8,
			max_total_audio_duration_seconds: 16,
			validation: { maxSize: 10485760 },
			configuration: { allow_inpaint: true }
		});

		expect(limits.kinds).toEqual(['image', 'video']);
		expect(limits.multiple).toBe(true);
		expect(limits.maxItems).toBe(4);
		expect(limits.maxResolution).toBe(2048);
		expect(limits.maxFileSizeBytes).toBe(10485760);
		expect(limits.maxVideoDurationSeconds).toBe(12);
		expect(limits.maxTotalVideoDurationSeconds).toBe(30);
		expect(limits.maxAudioDurationSeconds).toBe(8);
		expect(limits.maxTotalAudioDurationSeconds).toBe(16);
		expect(limits.allowInpaint).toBe(true);
	});

	// Video and Audio still emit a per-item duration cap in the older
	// `validation` block; a field that only carries that must still enforce it.
	it('falls back to the validation block for a per-item duration cap', () => {
		const limits = readMediaLoaderConfig({ accept: 'video/*', validation: { maxDuration: 30 } });
		expect(limits.maxVideoDurationSeconds).toBe(30);
		expect(limits.maxTotalVideoDurationSeconds).toBeNull();
	});

	it('reads the older per-axis caps from the validation block', () => {
		const limits = readMediaLoaderConfig({ validation: { maxWidth: 1024, maxHeight: 768 } });
		expect(limits.maxWidth).toBe(1024);
		expect(limits.maxHeight).toBe(768);
	});

	it('treats the authoring spelling `multi` as `multiple`', () => {
		expect(readMediaLoaderConfig({ configuration: { multi: true } }).multiple).toBe(true);
	});

	it('drops zero and negative limits rather than enforcing them', () => {
		const limits = readMediaLoaderConfig({ max_items: 0, validation: { maxSize: -1 } });
		expect(limits.maxItems).toBeNull();
		expect(limits.maxFileSizeBytes).toBeNull();
	});

	it('keeps the authored accept string for the file input', () => {
		expect(readMediaLoaderConfig({ accept: 'image/png' }).accept).toBe('image/png');
	});

	it('synthesizes an accept string from a kind list', () => {
		expect(acceptAttribute({ accepted_types: ['image', 'audio'] }, ['image', 'audio'])).toBe('image/*,audio/*');
	});

	// A `media` field's authored `accept` is the union of all three kinds
	// whatever `accepted_types` says, so echoing it would open a picker
	// offering audio the field then refuses.
	it('narrows the file picker when accepted_types narrows the field', () => {
		const config = {
			accept: 'image/png,video/mp4,audio/mpeg',
			accepted_types: ['image', 'video']
		};
		expect(readMediaLoaderConfig(config).accept).toBe('image/*,video/*');
	});

	it('keeps the authored accept string when nothing narrows it', () => {
		expect(readMediaLoaderConfig({ accept: 'image/png,image/webp' }).accept).toBe('image/png,image/webp');
	});
});

describe('copy', () => {
	it('names the accepted kinds in prose', () => {
		expect(describeKinds(['image'])).toBe('images');
		expect(describeKinds(['image', 'video'])).toBe('images and videos');
		expect(describeKinds(['image', 'video', 'audio'])).toBe('images, videos and audio');
	});

	it('names the drop target in the singular', () => {
		expect(describeDropTarget(['image'])).toBe('an image');
		expect(describeDropTarget(['image', 'video'])).toBe('an image or a video');
	});

	it('lists formats per accepted kind', () => {
		expect(describeFormats(['audio'])).toBe('WAV · MP3 · FLAC');
	});
});
