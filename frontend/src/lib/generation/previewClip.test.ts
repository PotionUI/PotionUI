import { describe, it, expect } from 'vitest';
import {
	adminFormatChip,
	adminStatusLine,
	chipLabel,
	deckStepText,
	clipCadence,
	frameOrigin,
	framesToAdvance,
	isBehind,
	lastFrameIndex,
	parsePreviewClip,
	wrapFrameIndex
} from './previewClip';

const wire = {
	generation_id: 'g1',
	image: 'data:image/webp;base64,AAAA',
	frames: 25,
	columns: 5,
	frame_width: 384,
	frame_height: 216,
	fps: 8,
	step: 15,
	total_steps: 30,
	source_indices: [0, 4, 8],
	admin_detail: { format: 'WEBP', bytes: 217088, long_edge: 384, update: 3, updates_max: 8, every: 4 }
};

describe('parsePreviewClip', () => {
	it('reads the wire payload', () => {
		const clip = parsePreviewClip(wire)!;
		expect(clip.frames).toBe(25);
		expect(clip.step).toBe(15);
		expect(clip.admin_detail?.every).toBe(4);
	});

	it('has no admin detail for a user payload', () => {
		const { admin_detail, ...user } = wire;
		expect(parsePreviewClip(user)!.admin_detail).toBeNull();
	});

	it('rejects malformed payloads', () => {
		expect(parsePreviewClip({ ...wire, image: 'http://x/y.webp' })).toBeNull();
		expect(parsePreviewClip({ ...wire, frames: 0 })).toBeNull();
		expect(parsePreviewClip({ ...wire, frames: 26 })).toBeNull();
		expect(parsePreviewClip({ ...wire, columns: undefined })).toBeNull();
		expect(parsePreviewClip(null)).toBeNull();
	});
});

describe('loop model', () => {
	it('locates frames in the sprite sheet row by row', () => {
		const clip = { columns: 5, frame_width: 384, frame_height: 216 };
		expect(frameOrigin(clip, 0)).toEqual({ sx: 0, sy: 0 });
		expect(frameOrigin(clip, 4)).toEqual({ sx: 1536, sy: 0 });
		expect(frameOrigin(clip, 7)).toEqual({ sx: 768, sy: 216 });
		expect(frameOrigin(clip, 24)).toEqual({ sx: 1536, sy: 864 });
	});

	it('wraps the frame index so a swapped clip keeps its position', () => {
		expect(wrapFrameIndex(26, 25)).toBe(1);
		expect(wrapFrameIndex(30, 12)).toBe(6);
		expect(wrapFrameIndex(-1, 25)).toBe(24);
		expect(wrapFrameIndex(3, 0)).toBe(0);
	});

	it('advances by whole frames at the clip rate', () => {
		expect(framesToAdvance(124, 8)).toBe(0);
		expect(framesToAdvance(125, 8)).toBe(1);
		expect(framesToAdvance(1000, 8)).toBe(8);
		expect(framesToAdvance(-5, 8)).toBe(0);
	});

	it('shows the last frame when motion is reduced', () => {
		expect(lastFrameIndex(25)).toBe(24);
		expect(lastFrameIndex(0)).toBe(0);
	});
});

describe('labels', () => {
	it('names the step the clip was decoded at', () => {
		expect(chipLabel(15)).toBe('Preview · Step 15');
		expect(chipLabel(0)).toBe('Preview');
	});

	it('is behind only when more than one update trails the live step', () => {
		expect(clipCadence(30)).toBe(3);
		expect(clipCadence(80)).toBe(10);
		expect(isBehind(15, 17, 3)).toBe(false);
		expect(isBehind(15, 18, 3)).toBe(false);
		expect(isBehind(15, 19, 3)).toBe(true);
		expect(isBehind(15, 0, 3)).toBe(false);
	});

	it('gives admins the format chip and the cadence line', () => {
		const clip = parsePreviewClip(wire)!;
		expect(adminFormatChip(clip)).toBe('384×216 · 25 fr · 8 fps');
		expect(adminStatusLine(clip.admin_detail!, 25)).toBe('Preview · 25 frames · new clip every 4 steps · 212 KB');
	});
});

describe('deckStepText', () => {
	it('keeps the plain step text without a clip', () => {
		expect(deckStepText('17/30', 0)).toBe('Step 17 / 30');
	});

	it('names the step the showing clip was decoded at', () => {
		expect(deckStepText('17/30', 15)).toBe('Step 17 of 30 · preview from step 15');
	});

	it('has no text without a step count', () => {
		expect(deckStepText(null, 15)).toBeNull();
		expect(deckStepText('sampling', 15)).toBeNull();
	});
});
