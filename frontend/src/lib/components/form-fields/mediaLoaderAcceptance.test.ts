import { describe, it, expect } from 'vitest';
import { readMediaLoaderConfig } from './mediaLoaderConfig';
import {
	describeCandidate,
	evaluateCandidate,
	summarizeContents,
	type MediaCandidate,
	type MediaLoaderContents
} from './mediaLoaderAcceptance';

const imageOnly = readMediaLoaderConfig({ accept: 'image/*' });

function candidate(overrides: Partial<MediaCandidate> = {}): MediaCandidate {
	return { name: 'shot.png', kind: 'image', mimeType: 'image/png', ...overrides };
}

function contents(overrides: Partial<MediaLoaderContents> = {}): MediaLoaderContents {
	return { count: 0, countByKind: {}, durationByKind: {}, durationUnknownByKind: {}, ...overrides };
}

function reasonsFor(
	c: MediaCandidate,
	limits = imageOnly,
	held?: MediaLoaderContents,
	fieldName?: string
): string[] {
	const verdict = evaluateCandidate(c, limits, held, { fieldName });
	if (verdict.accepted) throw new Error('expected a rejection');
	return verdict.reasons;
}

describe('evaluateCandidate', () => {
	it('accepts a file of an accepted kind', () => {
		expect(evaluateCandidate(candidate(), imageOnly)).toEqual({ accepted: true });
	});

	// The server says: "item 2: type 'video' is not accepted for 'ref'
	// (accepted: image)". Same clause, minus the item index the candidate does
	// not occupy yet.
	it('rejects a kind the field does not take, in the words the server uses', () => {
		const reasons = reasonsFor(
			candidate({ name: 'take_04.mov', kind: 'video', mimeType: 'video/quicktime' }),
			imageOnly,
			undefined,
			'reference_image'
		);
		expect(reasons).toEqual(["Type 'video' is not accepted for 'reference_image' (accepted: image)"]);
	});

	it('lists the accepted kinds in the same sorted order the server does', () => {
		const limits = readMediaLoaderConfig({ accepted_types: ['video', 'image'] });
		expect(reasonsFor(candidate({ kind: 'audio' }), limits)[0]).toContain('(accepted: image, video)');
	});

	it('rejects a file whose kind could not be read at all', () => {
		expect(reasonsFor(candidate({ kind: null, name: 'notes.txt' }))[0]).toContain('notes.txt');
	});

	it('accepts a second kind when the field declares both', () => {
		const both = readMediaLoaderConfig({ accept: 'image/*,video/*' });
		expect(evaluateCandidate(candidate({ kind: 'video' }), both)).toEqual({ accepted: true });
	});

	it('rejects once every slot is used, naming the cap', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', multiple: true, max_items: 4 });
		expect(reasonsFor(candidate(), limits, contents({ count: 4, countByKind: { image: 4 } }), 'refs')).toEqual([
			"Too many items for 'refs': maximum is 4"
		]);
	});

	// Single-item fields replace rather than append, so a held value is not a
	// reason to refuse the next one.
	it('does not apply the item cap outside multi mode', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', max_items: 1 });
		expect(
			evaluateCandidate(candidate(), limits, contents({ count: 1, countByKind: { image: 1 } }))
		).toEqual({ accepted: true });
	});

	it('rejects an oversized file, naming both the cap and the actual size', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', validation: { maxSize: 10 * 1024 * 1024 } });
		expect(reasonsFor(candidate({ sizeBytes: 42 * 1024 * 1024 }), limits)).toEqual([
			'File size 42 MB exceeds the maximum of 10 MB'
		]);
	});

	it('rejects an oversized image per axis, in the words the server uses', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', max_resolution: 2048 });
		expect(reasonsFor(candidate({ width: 4096, height: 3072 }), limits)).toEqual([
			'Width 4096px exceeds the maximum resolution of 2048px',
			'Height 3072px exceeds the maximum resolution of 2048px'
		]);
	});

	// `max_resolution` caps each axis separately, exactly as the backend's
	// `_check_media_constraints` does - a long thin image is not "within" it.
	it('applies the resolution cap per axis, not to the bounding box', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', max_resolution: 2048 });
		expect(reasonsFor(candidate({ width: 4096, height: 512 }), limits)).toEqual([
			'Width 4096px exceeds the maximum resolution of 2048px'
		]);
		expect(evaluateCandidate(candidate({ width: 2048, height: 2048 }), limits)).toEqual({ accepted: true });
	});

	it('never applies a resolution cap to audio, which has none', () => {
		const limits = readMediaLoaderConfig({ accepted_types: ['audio'], max_resolution: 64 });
		expect(evaluateCandidate(candidate({ kind: 'audio', width: 4096, height: 4096 }), limits)).toEqual({
			accepted: true
		});
	});

	it('still honours the older per-axis caps', () => {
		const limits = readMediaLoaderConfig({ accept: 'image/*', validation: { maxWidth: 1024, maxHeight: 1024 } });
		expect(reasonsFor(candidate({ width: 2048, height: 512 }), limits)).toEqual([
			'Width 2048px exceeds the maximum of 1024px'
		]);
	});

	it('rejects a clip longer than the per-item limit', () => {
		const limits = readMediaLoaderConfig({ accept: 'video/*', max_video_duration_seconds: 5 });
		expect(reasonsFor(candidate({ kind: 'video', durationSeconds: 8.4 }), limits)).toEqual([
			'Video duration 8.4s exceeds the per-video maximum of 5s'
		]);
	});

	it('rejects a clip that would push the total over the limit, naming the total it would reach', () => {
		const limits = readMediaLoaderConfig({
			accept: 'video/*',
			multiple: true,
			max_total_video_duration_seconds: 20
		});
		const held = contents({ count: 2, countByKind: { video: 2 }, durationByKind: { video: 18 } });
		expect(reasonsFor(candidate({ kind: 'video', durationSeconds: 8 }), limits, held, 'clips')).toEqual([
			"Video items total 26s of duration for 'clips', exceeding the maximum of 20s"
		]);
	});

	it('applies audio duration limits to audio, not to video', () => {
		const limits = readMediaLoaderConfig({
			accepted_types: ['video', 'audio'],
			max_audio_duration_seconds: 5
		});
		expect(evaluateCandidate(candidate({ kind: 'video', durationSeconds: 30 }), limits)).toEqual({
			accepted: true
		});
		expect(reasonsFor(candidate({ kind: 'audio', durationSeconds: 30 }), limits)[0]).toBe(
			'Audio duration 30s exceeds the per-audio maximum of 5s'
		);
	});

	it('keeps the two budgets independent', () => {
		const limits = readMediaLoaderConfig({
			accepted_types: ['video', 'audio'],
			multiple: true,
			max_total_video_duration_seconds: 10,
			max_total_audio_duration_seconds: 60
		});
		const held = contents({
			count: 2,
			countByKind: { video: 1, audio: 1 },
			durationByKind: { video: 9, audio: 30 }
		});
		// Audio has 30s of headroom left even though video has none.
		expect(evaluateCandidate(candidate({ kind: 'audio', durationSeconds: 20 }), limits, held)).toEqual({
			accepted: true
		});
		expect(reasonsFor(candidate({ kind: 'video', durationSeconds: 5 }), limits, held)).toHaveLength(1);
	});

	it('reports every violation at once, not just the first', () => {
		const limits = readMediaLoaderConfig({
			accept: 'video/*',
			multiple: true,
			max_resolution: 1080,
			max_video_duration_seconds: 5,
			validation: { maxSize: 1024 * 1024 }
		});
		const reasons = reasonsFor(
			candidate({ kind: 'video', width: 3840, height: 2160, durationSeconds: 12, sizeBytes: 40 * 1024 * 1024 }),
			limits
		);
		expect(reasons).toHaveLength(4);
	});

	describe('unknown metadata fails open', () => {
		it('skips a limit whose input was never measured', () => {
			const limits = readMediaLoaderConfig({
				accept: 'video/*',
				max_resolution: 64,
				max_video_duration_seconds: 1
			});
			expect(evaluateCandidate(candidate({ kind: 'video' }), limits)).toEqual({ accepted: true });
		});

		// The trap this mirrors: one held clip of unknown length makes the sum
		// partial, and a partial sum can pass a budget the full one would fail
		// - so the server skips the total outright, and so must this.
		it('skips a total entirely when a held item of that kind has no duration', () => {
			const limits = readMediaLoaderConfig({
				accept: 'video/*',
				multiple: true,
				max_total_video_duration_seconds: 20
			});
			const held = contents({
				count: 2,
				countByKind: { video: 2 },
				durationByKind: { video: 18 },
				durationUnknownByKind: { video: true }
			});
			expect(evaluateCandidate(candidate({ kind: 'video', durationSeconds: 8 }), limits, held)).toEqual({
				accepted: true
			});
		});

		it('still enforces the per-item limit when only the total is unknowable', () => {
			const limits = readMediaLoaderConfig({
				accept: 'video/*',
				multiple: true,
				max_video_duration_seconds: 5,
				max_total_video_duration_seconds: 20
			});
			const held = contents({ count: 1, countByKind: { video: 1 }, durationUnknownByKind: { video: true } });
			expect(reasonsFor(candidate({ kind: 'video', durationSeconds: 8 }), limits, held)).toEqual([
				'Video duration 8s exceeds the per-video maximum of 5s'
			]);
		});
	});
});

describe('describeCandidate', () => {
	it('identifies the file that was refused', () => {
		expect(describeCandidate(candidate({ name: 'take_04.mov', mimeType: 'video/quicktime' }))).toBe(
			'take_04.mov · video/quicktime'
		);
	});
});

describe('summarizeContents', () => {
	const kindOf = (item: unknown) => (item as { kind: 'video' | 'audio' | 'image' }).kind;
	const durationOf = (item: unknown) => (item as { duration: number | null }).duration;

	it('tallies counts and durations per kind', () => {
		const summary = summarizeContents(
			[
				{ kind: 'video', duration: 4 },
				{ kind: 'video', duration: 6 },
				{ kind: 'image', duration: null }
			],
			kindOf,
			durationOf
		);
		expect(summary.count).toBe(3);
		expect(summary.countByKind).toEqual({ video: 2, image: 1 });
		expect(summary.durationByKind).toEqual({ video: 10 });
		expect(summary.durationUnknownByKind).toEqual({});
	});

	it('marks a category unknown when one of its items reported no duration', () => {
		const summary = summarizeContents(
			[
				{ kind: 'video', duration: 4 },
				{ kind: 'video', duration: null }
			],
			kindOf,
			durationOf
		);
		expect(summary.durationByKind).toEqual({ video: 4 });
		expect(summary.durationUnknownByKind).toEqual({ video: true });
	});

	// An image has no duration by nature; that is not the same as a clip whose
	// length could not be read, and it must not poison anything.
	it('does not treat a still as an unknown duration', () => {
		const summary = summarizeContents([{ kind: 'image', duration: null }], kindOf, durationOf);
		expect(summary.durationUnknownByKind).toEqual({});
	});
});
