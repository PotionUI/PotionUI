import { describe, it, expect } from 'vitest';
import {
	parseMusicDirectorCapabilities,
	resolveMusicDirectorCapabilities,
	musicReferencesGate,
	musicModeHasSections,
	createDefaultMusicDirectorValue,
	normalizeMusicDirectorValue,
	deriveMusicDirectorMode,
	validateMusicDirector,
	buildMusicDirectorSubmission,
	appendQuickSection,
	addMusicReference,
	removeMusicReference,
	mintReferenceId,
	applyMusicDirectorOperations,
	matchSectionKind,
	canonicalizeSectionKind
} from './musicDirector';
import type { MusicDirectorValue } from '$lib/types/musicDirector';
import { SECTION_KINDS } from '$lib/types/musicDirector';
import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';

function seg(name: string, content: string, overrides: Partial<Segment> = {}): Segment {
	return { id: overrides.id ?? `seg-${name}-${content}`, content, type: 'content', enabled: true, chips: {}, name, ...overrides };
}

const media = (path: string): MediaRef => ({ path, type: 'audio' });

// Three fixtures mirroring docs/music-director.md's worked examples.
const MUSIC3_RAW_CAPS = {
	preset_modes: ['song'],
	modes: {
		t2m: {},
		song: {},
		director: { max_sections: 12, per_section_prompts: true, references: 'whole', compile: 'single_shot' }
	},
	settings: { bpm: false, key: false, time_signature: false },
	limits: { default_duration: 120, max_duration: 240, sample_rate: 44100, stereo: true }
};

const ACE_RAW_CAPS = {
	preset_modes: ['song'],
	modes: {
		t2m: {},
		song: {},
		style: { max_reference_seconds: 30 },
		repaint: {},
		director: { max_sections: 12, per_section_prompts: true, references: 'per_section', compile: 'single_shot' }
	},
	settings: { bpm: true, key: false, time_signature: false },
	limits: { default_duration: 120, max_duration: 300, sample_rate: 44100, stereo: true }
};

const YUE_RAW_CAPS = {
	preset_modes: ['song'],
	modes: { t2m: {}, song: {}, extend: {} },
	settings: { bpm: false, key: false, time_signature: false },
	limits: { default_duration: 120, max_duration: 180, sample_rate: 32000, stereo: true }
};

describe('parseMusicDirectorCapabilities', () => {
	it('returns null for non-object input or a shape with no modes', () => {
		expect(parseMusicDirectorCapabilities(null)).toBeNull();
		expect(parseMusicDirectorCapabilities(undefined)).toBeNull();
		expect(parseMusicDirectorCapabilities({})).toBeNull();
		expect(parseMusicDirectorCapabilities({ modes: {} })).toBeNull();
	});

	it('parses a Music3-like preset: song + director, single_shot, no settings knobs', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		expect(caps.enabledModes).toEqual(['t2m', 'song', 'director']);
		expect(caps.modes.director).toEqual({
			maxReferenceSeconds: null,
			maxSections: 12,
			perSectionPrompts: true,
			sectionDurationHints: false,
			references: 'whole',
			compile: 'single_shot',
			descriptionRequired: false
		});
		expect(caps.settings).toEqual({ bpm: false, key: false, timeSignature: false });
		expect(caps.limits.maxDuration).toBe(240);
		expect(caps.formOwnsSettings).toBe(false);
	});

	it('parses form_owns_settings when the preset declares it', () => {
		const caps = parseMusicDirectorCapabilities({ ...MUSIC3_RAW_CAPS, form_owns_settings: true })!;
		expect(caps.formOwnsSettings).toBe(true);
	});

	it('parses an ACE-like preset: adds style/repaint, per_section references, bpm true', () => {
		const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;
		expect(caps.enabledModes).toEqual(['t2m', 'song', 'style', 'director', 'repaint']);
		expect(caps.modes.style?.maxReferenceSeconds).toBe(30);
		expect(caps.modes.director?.references).toBe('per_section');
		expect(caps.settings.bpm).toBe(true);
	});

	it('parses a YuE-like preset: song + extend, no director', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		expect(caps.enabledModes).toEqual(['t2m', 'song', 'extend']);
		expect(caps.modes.director).toBeUndefined();
		expect(caps.limits.maxDuration).toBe(180);
	});

	it('defaults max_sections to 12 when the director capability omits it', () => {
		const caps = parseMusicDirectorCapabilities({ modes: { director: {} } })!;
		expect(caps.modes.director?.maxSections).toBe(12);
	});
});

describe('resolveMusicDirectorCapabilities (preset_mode_overrides overlay)', () => {
	it('shallow-merges a top-level key and per-mode merges `modes`', () => {
		const raw = {
			...ACE_RAW_CAPS,
			preset_mode_overrides: {
				instrumental: {
					modes: { style: { max_reference_seconds: 60 }, director: null }
				}
			}
		};
		const overridden = resolveMusicDirectorCapabilities(raw, 'instrumental')!;
		expect(overridden.modes.style?.maxReferenceSeconds).toBe(60);
		expect(overridden.modes.director).toBeUndefined();
		// Unrelated mode untouched by the override.
		expect(overridden.modes.repaint).toBeDefined();
	});

	it('falls back to the base capabilities when the preset mode has no override', () => {
		const base = parseMusicDirectorCapabilities(ACE_RAW_CAPS);
		const resolved = resolveMusicDirectorCapabilities(ACE_RAW_CAPS, 'song');
		expect(resolved).toEqual(base);
	});
});

describe('musicReferencesGate', () => {
	const musicCaps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
	const aceCaps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;

	it('style always allows and requires references', () => {
		expect(musicReferencesGate('style', aceCaps)).toEqual({ allowed: true, required: true });
	});

	it('song allows references only when the preset also declares style', () => {
		expect(musicReferencesGate('song', aceCaps)).toEqual({ allowed: true, required: false });
		expect(musicReferencesGate('song', musicCaps)).toEqual({ allowed: false, required: false });
	});

	it('director follows its own references capability', () => {
		expect(musicReferencesGate('director', musicCaps)).toEqual({ allowed: true, required: false }); // 'whole'
		expect(musicReferencesGate('director', aceCaps)).toEqual({ allowed: true, required: false }); // 'per_section'
	});

	it('t2m/extend/repaint never accept references', () => {
		expect(musicReferencesGate('t2m', aceCaps).allowed).toBe(false);
		expect(musicReferencesGate('repaint', aceCaps).allowed).toBe(false);
	});
});

describe('SECTION_KINDS', () => {
	it('is the full closed set the backend accepts, in order', () => {
		expect(SECTION_KINDS).toEqual([
			'intro',
			'verse',
			'pre_chorus',
			'chorus',
			'post_chorus',
			'bridge',
			'instrumental',
			'solo',
			'outro'
		]);
	});
});

describe('matchSectionKind / canonicalizeSectionKind', () => {
	it('matches every kind by its display label, case-insensitively', () => {
		expect(matchSectionKind('Pre-Chorus')).toBe('pre_chorus');
		expect(matchSectionKind('pre-chorus')).toBe('pre_chorus');
		expect(matchSectionKind('CHORUS')).toBe('chorus');
	});

	it('matches by the raw slug, underscore or hyphen', () => {
		expect(matchSectionKind('post_chorus')).toBe('post_chorus');
		expect(matchSectionKind('post-chorus')).toBe('post_chorus');
	});

	it('tolerates a trailing counter a repeated quick-add leaves behind', () => {
		expect(matchSectionKind('Verse 2')).toBe('verse');
		expect(matchSectionKind('Chorus-3')).toBe('chorus');
	});

	it('empty/absent name matches verse', () => {
		expect(matchSectionKind(null)).toBe('verse');
		expect(matchSectionKind(undefined)).toBe('verse');
		expect(matchSectionKind('   ')).toBe('verse');
	});

	it('an unrecognized name returns null so validation can catch it', () => {
		expect(matchSectionKind('Breakdown')).toBeNull();
	});

	it('canonicalizeSectionKind falls back to verse for an unrecognized name', () => {
		expect(canonicalizeSectionKind('Breakdown')).toBe('verse');
	});
});

describe('musicModeHasSections', () => {
	it('is true only for song and director', () => {
		expect(musicModeHasSections('song')).toBe(true);
		expect(musicModeHasSections('director')).toBe(true);
		expect(musicModeHasSections('t2m')).toBe(false);
		expect(musicModeHasSections('style')).toBe(false);
		expect(musicModeHasSections('extend')).toBe(false);
		expect(musicModeHasSections('repaint')).toBe(false);
	});
});

describe('deriveMusicDirectorMode', () => {
	const musicCaps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!; // t2m, song, director (no style/extend/repaint)
	const aceCaps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!; // t2m, song, style, director, repaint
	const yueCaps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!; // t2m, song, extend (no director)

	it('a fresh/empty document derives to t2m, never an invalid empty song', () => {
		const value = createDefaultMusicDirectorValue(musicCaps);
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('t2m');
	});

	it('a single plain lyrics segment derives to song', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(musicCaps), segments: [seg('Verse', 'la la')] };
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('song');
	});

	it('more than one segment derives to director', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(musicCaps), segments: [seg('Verse', 'a'), seg('Chorus', 'b')] };
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('director');
	});

	it('a disabled or break segment does not count toward the section total', () => {
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(musicCaps),
			segments: [seg('Verse', 'a'), seg('Chorus', 'b', { enabled: false }), seg('', '', { type: 'break' })]
		};
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('song');
	});

	it('multi-segment richness is ignored when the preset never declares director', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(yueCaps), segments: [seg('Verse', 'a'), seg('Chorus', 'b')] };
		expect(deriveMusicDirectorMode(value, yueCaps)).toBe('song');
	});

	it('the Instrumental toggle derives t2m when the preset declares it, regardless of an untouched lyrics segment', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(musicCaps), instrumental: true };
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('t2m');
	});

	it('a real section timeline always wins over the Instrumental toggle', () => {
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(musicCaps),
			instrumental: true,
			segments: [seg('Verse', 'a'), seg('Chorus', 'b')]
		};
		expect(deriveMusicDirectorMode(value, musicCaps)).toBe('director');
	});

	it('derives extend from a populated extend_source', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(yueCaps), extend_source: { media: media('x.wav') } };
		expect(deriveMusicDirectorMode(value, yueCaps)).toBe('extend');
	});

	it('derives repaint from a populated repaint.source', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(aceCaps), repaint: { source: { media: media('x.wav') }, start: 0, end: 10 } };
		expect(deriveMusicDirectorMode(value, aceCaps)).toBe('repaint');
	});

	it('derives style from references with no section content, when the preset declares style', () => {
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(aceCaps), references: [{ id: 'r1', media: media('x.wav') }] };
		expect(deriveMusicDirectorMode(value, aceCaps)).toBe('style');
	});

	it('never derives a mode outside the capability set', () => {
		for (const caps of [musicCaps, aceCaps, yueCaps]) {
			for (const instrumental of [false, true]) {
				const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), instrumental };
				expect(caps.enabledModes).toContain(deriveMusicDirectorMode(value, caps));
			}
		}
	});
});

describe('createDefaultMusicDirectorValue / normalizeMusicDirectorValue', () => {
	it('seeds one default segment for a sectioned first-enabled mode', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		const def = createDefaultMusicDirectorValue(caps);
		expect(def.mode).toBe('t2m');
		expect(def.segments).toEqual([]);
	});

	it('normalize is idempotent and re-shapes garbage input to the default', () => {
		const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;
		const normalized = normalizeMusicDirectorValue({ garbage: true, mode: 'nope' }, caps);
		const again = normalizeMusicDirectorValue(normalized, caps);
		expect(normalized).toEqual(again);
	});

	it('strips bpm/key/time_signature the capability does not declare', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!; // settings all false
		const normalized = normalizeMusicDirectorValue(
			{ mode: 'song', segments: [{ id: 's1', type: 'content', content: 'x', name: 'Verse', enabled: true }], settings: { bpm: 92, key: 'C minor' } },
			caps
		);
		expect(normalized.settings.bpm).toBeNull();
		expect(normalized.settings.key).toBeNull();
	});

	it('keeps a declared bpm', () => {
		const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!; // bpm: true
		const normalized = normalizeMusicDirectorValue({ mode: 'song', settings: { bpm: 92 } }, caps);
		expect(normalized.settings.bpm).toBe(92);
	});

	it('loads an already segment-shaped document, preserving segment ids', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const normalized = normalizeMusicDirectorValue(
			{ mode: 'song', segments: [{ id: 'kept-id', type: 'content', content: 'authored line', name: 'Verse', enabled: true, chips: {} }] },
			caps
		);
		expect(normalized.segments).toEqual([expect.objectContaining({ id: 'kept-id', content: 'authored line', name: 'Verse' })]);
	});

	it('converts a history document carrying the old sections (kind + lyrics) shape forward, one segment per section', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const normalized = normalizeMusicDirectorValue(
			{
				mode: 'song',
				sections: [
					{ id: 's1', kind: 'verse', lyrics: 'verse one' },
					{ id: 's2', kind: 'chorus', lyrics: 'chorus one' }
				]
			},
			caps
		);
		expect(normalized.segments).toHaveLength(2);
		expect(normalized.segments[0]).toMatchObject({ name: 'Verse', content: 'verse one', type: 'content', enabled: true });
		expect(normalized.segments[1]).toMatchObject({ name: 'Chorus', content: 'chorus one' });
	});

	it('is idempotent after an old-shape migration: re-normalizing keeps the migrated segments', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const once = normalizeMusicDirectorValue({ mode: 'song', sections: [{ id: 's1', kind: 'verse', lyrics: 'roundtrip me' }] }, caps);
		const twice = normalizeMusicDirectorValue(once, caps);
		expect(twice.segments).toEqual(once.segments);
	});

	it('falls back to a default segment when segments is present but empty and there is no old sections to migrate', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const normalized = normalizeMusicDirectorValue({ mode: 'song', segments: [] }, caps);
		expect(normalized.segments).toHaveLength(1);
	});
});

describe('appendQuickSection', () => {
	it('appends a fresh content segment carrying the kind label and color', () => {
		const next = appendQuickSection([], 'pre_chorus');
		expect(next).toHaveLength(1);
		expect(next[0]).toMatchObject({ type: 'content', name: 'Pre-Chorus', content: '', enabled: true });
		expect(next[0].color).toBeTruthy();
	});

	it('never mutates the input array', () => {
		const base = [seg('Verse', 'a')];
		appendQuickSection(base, 'chorus');
		expect(base).toHaveLength(1);
	});
});

describe('reference operations', () => {
	it('add/remove/mint are pure and never mint an id themselves', () => {
		const refs = [{ id: 'ref-1', media: media('a.wav') }];
		const id = mintReferenceId(refs);
		expect(id).toBe('ref-2');
		const added = addMusicReference(refs, id, media('b.wav'));
		expect(added.map((r) => r.id)).toEqual(['ref-1', 'ref-2']);
		expect(refs.map((r) => r.id)).toEqual(['ref-1']); // untouched
		expect(removeMusicReference(added, 'ref-1').map((r) => r.id)).toEqual(['ref-2']);
	});
});

// A preset that declares `song` (and `director`) but NOT `t2m` -- exercises
// the "empty document" validation path deriveMusicDirectorMode otherwise
// steers around by degrading to `t2m` (see the deriveMusicDirectorMode
// describe block above): with no lyrics-less mode available at all, an empty
// document has nowhere to land but an invalid `song`.
const SONG_ONLY_RAW_CAPS = {
	preset_modes: ['song'],
	modes: { song: {}, director: { max_sections: 12, compile: 'single_shot' } },
	settings: { bpm: false, key: false, time_signature: false },
	limits: { default_duration: 60, max_duration: 240, sample_rate: 44100, stereo: true }
};

describe('validateMusicDirector', () => {
	it('rejects an empty song when the preset offers no lyrics-less fallback mode', () => {
		const caps = parseMusicDirectorCapabilities(SONG_ONLY_RAW_CAPS)!;
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), segments: [] };
		expect(deriveMusicDirectorMode(value, caps)).toBe('song'); // nowhere else to land
		const result = validateMusicDirector(value, caps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain('Lyrics require at least one section');
	});

	it('an empty document on a preset that also declares t2m degrades to a valid t2m submission instead of an invalid song', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		const value = createDefaultMusicDirectorValue(caps);
		expect(validateMusicDirector(value, caps)).toEqual({ ok: true, reasons: [] });
	});

	it('rejects an empty/whitespace-only description when the mode declares description_required (real MiniMax-Music3 bug: empty caption reaches the pipe as "nothing to generate from")', () => {
		const requiredCaps = {
			...MUSIC3_RAW_CAPS,
			modes: {
				t2m: { description_required: true },
				song: { description_required: true },
				director: { ...MUSIC3_RAW_CAPS.modes.director, description_required: true }
			}
		};
		const caps = parseMusicDirectorCapabilities(requiredCaps)!;

		const empty = createDefaultMusicDirectorValue(caps);
		expect(deriveMusicDirectorMode(empty, caps)).toBe('t2m');
		const emptyResult = validateMusicDirector(empty, caps);
		expect(emptyResult.ok).toBe(false);
		expect(emptyResult.reasons).toContain('A style description is required -- there is nothing to generate music from otherwise');

		const whitespace = { ...empty, description: '   ' };
		expect(validateMusicDirector(whitespace, caps).ok).toBe(false);

		const filled = { ...empty, description: 'warm 90s boom-bap' };
		expect(validateMusicDirector(filled, caps)).toEqual({ ok: true, reasons: [] });
	});

	it('leaves description optional when the mode does not declare description_required (default, unchanged behavior)', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		const value = createDefaultMusicDirectorValue(caps);
		expect(value.description).toBe('');
		expect(validateMusicDirector(value, caps)).toEqual({ ok: true, reasons: [] });
	});

	it('skips the description_required/duration checks when the preset form owns settings', () => {
		const caps = parseMusicDirectorCapabilities({
			...MUSIC3_RAW_CAPS,
			form_owns_settings: true,
			modes: { ...MUSIC3_RAW_CAPS.modes, t2m: { description_required: true } }
		})!;
		const value = createDefaultMusicDirectorValue(caps);
		expect(value.description).toBe('');
		expect(validateMusicDirector(value, caps)).toEqual({ ok: true, reasons: [] });
	});

	it('accepts a single-segment song', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const value = normalizeMusicDirectorValue({ mode: 'song', segments: [{ id: 's1', type: 'content', content: 'la la', name: 'Verse', enabled: true }] }, caps);
		expect(validateMusicDirector(value, caps)).toEqual({ ok: true, reasons: [] });
	});

	it('rejects a segment whose name does not canonicalize to a known section kind', () => {
		const caps = parseMusicDirectorCapabilities(SONG_ONLY_RAW_CAPS)!;
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), segments: [seg('Breakdown', 'x')] };
		const result = validateMusicDirector(value, caps);
		expect(result.ok).toBe(false);
		expect(result.reasons.some((r) => r.includes('Breakdown') && r.includes('not a recognized section kind'))).toBe(true);
	});

	it('rejects too many segments in director mode', () => {
		const caps = parseMusicDirectorCapabilities({
			...MUSIC3_RAW_CAPS,
			modes: { ...MUSIC3_RAW_CAPS.modes, director: { ...MUSIC3_RAW_CAPS.modes.director, max_sections: 2 } }
		})!;
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(caps),
			segments: [seg('Verse', 'a'), seg('Chorus', 'b'), seg('Outro', 'c')]
		};
		expect(deriveMusicDirectorMode(value, caps)).toBe('director');
		const result = validateMusicDirector(value, caps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain('Too many sections (max 2)');
	});

	it('rejects references on style mode exceeding a declared duration hint', () => {
		const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;
		const value = normalizeMusicDirectorValue(
			{ mode: 'style', references: [{ id: 'r1', media: { path: 'x.wav', type: 'audio', duration_seconds: 45 } }] },
			caps
		);
		const result = validateMusicDirector(value, caps);
		expect(result.ok).toBe(false);
		expect(result.reasons.some((r) => r.includes('exceeds'))).toBe(true);
	});

	it('deriveMusicDirectorMode never resolves to a mode outside a real capability set (validateMusicDirector always finds a cap)', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!; // no style
		const value = { ...createDefaultMusicDirectorValue(caps), mode: 'style' as const };
		expect(deriveMusicDirectorMode(value, caps)).not.toBe('style');
		expect(validateMusicDirector(value, caps).ok).toBe(true);
	});

	it('rejects duration over the capability max', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const value = normalizeMusicDirectorValue({ mode: 't2m', settings: { duration: 999 } }, caps);
		const result = validateMusicDirector(value, caps);
		expect(result.ok).toBe(false);
		expect(result.reasons.some((r) => r.includes('maximum'))).toBe(true);
	});
});

describe('buildMusicDirectorSubmission', () => {
	it('emits the single-section object shorthand for a one-segment song', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const value = normalizeMusicDirectorValue(
			{ mode: 'song', description: 'boom bap', segments: [{ id: 's1', type: 'content', content: 'la la', name: 'Verse', enabled: true }], settings: { duration: 120, seed: 5 } },
			caps
		);
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(Array.isArray(wire.sections)).toBe(false);
		const section = wire.sections as { id: string; kind: string; lyrics: string };
		expect(section).toMatchObject({ id: 's1', kind: 'verse', lyrics: 'la la' });
		expect(wire.segments).toEqual(value.segments);
	});

	it('emits a list for a multi-segment song, canonicalizing each segment name to its kind', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const value = normalizeMusicDirectorValue({ mode: 'song', segments: [{ id: 's1', type: 'content', content: 'a', name: 'Verse', enabled: true }, { id: 's2', type: 'content', content: 'b', name: 'Chorus', enabled: true }] }, caps);
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(Array.isArray(wire.sections)).toBe(true);
		expect(wire.sections).toEqual([
			{ id: 's1', kind: 'verse', lyrics: 'a' },
			{ id: 's2', kind: 'chorus', lyrics: 'b' }
		]);
	});

	it('a two-segment document always emits a list, even when the derived mode is director', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), segments: [seg('Chorus', 'x'), seg('Verse', 'y')] };
		expect(deriveMusicDirectorMode(value, caps)).toBe('director');
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(Array.isArray(wire.sections)).toBe(true);
	});

	it('skips disabled and break segments when building sections', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(caps),
			segments: [seg('Verse', 'kept'), seg('Chorus', 'dropped', { enabled: false }), seg('', '', { type: 'break' })]
		};
		const wire = buildMusicDirectorSubmission(value, caps);
		const section = wire.sections as { lyrics: string };
		expect(section.lyrics).toBe('kept');
	});

	it('canonicalizes an unknown segment name to verse rather than throwing', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!;
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), segments: [seg('Breakdown', 'x'), seg('Verse', 'y')] };
		const wire = buildMusicDirectorSubmission(value, caps);
		const sections = wire.sections as { kind: string }[];
		expect(sections[0].kind).toBe('verse');
	});

	it('resolves chip content the same way the prompt resolver does', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const chipped = seg('Verse', '#color roses', {
			chips: {
				c1: { id: 'c1', categoryPath: 'color', valueId: 'v1', label: 'Red', value: 'red', allValues: [], shuffle: false, autoRegen: false }
			}
		});
		const value: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), segments: [chipped] };
		const wire = buildMusicDirectorSubmission(value, caps);
		const section = wire.sections as { lyrics: string };
		expect(section.lyrics).toBe('red roses');
	});

	it('never emits settings.bpm/key/time_signature when the capability does not declare it', () => {
		const caps = parseMusicDirectorCapabilities(MUSIC3_RAW_CAPS)!; // all false
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(caps),
			mode: 't2m',
			settings: { duration: 120, seed: -1, bpm: 92, key: 'C', time_signature: '4/4' }
		};
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(wire.settings.bpm).toBeUndefined();
		expect(wire.settings.key).toBeUndefined();
		expect(wire.settings.time_signature).toBeUndefined();
	});

	it('emits settings.bpm when the capability declares it', () => {
		const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!; // bpm: true
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(caps),
			mode: 'song',
			segments: [seg('Verse', 'x')],
			settings: { duration: 120, seed: -1, bpm: 92, key: null, time_signature: null }
		};
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(wire.settings.bpm).toBe(92);
	});

	it('emits references only when the mode gate allows them', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!; // song, no style declared
		const value: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(caps),
			mode: 'song',
			segments: [seg('Verse', 'x')],
			references: [{ id: 'r1', media: media('x.wav') }]
		};
		const wire = buildMusicDirectorSubmission(value, caps);
		expect(wire.references).toBeUndefined();
	});

	it('round-trips extend_source and repaint', () => {
		const caps = parseMusicDirectorCapabilities(YUE_RAW_CAPS)!;
		const extendValue: MusicDirectorValue = { ...createDefaultMusicDirectorValue(caps), mode: 'extend', extend_source: { media: media('x.wav') } };
		expect(buildMusicDirectorSubmission(extendValue, caps).extend_source).toEqual({ media: media('x.wav') });

		const aceCaps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;
		const repaintValue: MusicDirectorValue = {
			...createDefaultMusicDirectorValue(aceCaps),
			mode: 'repaint',
			repaint: { source: { media: media('x.wav') }, start: 5, end: 12 }
		};
		expect(buildMusicDirectorSubmission(repaintValue, aceCaps).repaint).toEqual({ source: { media: media('x.wav') }, start: 5, end: 12 });
	});
});

describe('applyMusicDirectorOperations', () => {
	const caps = parseMusicDirectorCapabilities(ACE_RAW_CAPS)!;
	const base = createDefaultMusicDirectorValue(caps);

	it('returns the value unchanged for malformed operations input', () => {
		expect(applyMusicDirectorOperations(base, null, caps)).toEqual(base);
		expect(applyMusicDirectorOperations(base, undefined, caps)).toEqual(base);
		expect(applyMusicDirectorOperations(base, 'nope', caps)).toEqual(base);
	});

	it('skips an entry with no string op, or an unrecognized op', () => {
		expect(applyMusicDirectorOperations(base, [{}], caps)).toEqual(base);
		expect(applyMusicDirectorOperations(base, [{ op: 'set_mode', mode: 'song' }], caps)).toEqual(base);
	});

	it('set_description replaces the description', () => {
		const next = applyMusicDirectorOperations(base, [{ op: 'set_description', description: 'warm boom-bap' }], caps);
		expect(next.description).toBe('warm boom-bap');
	});

	it('set_description ignores a non-string value', () => {
		const next = applyMusicDirectorOperations(base, [{ op: 'set_description', description: 42 }], caps);
		expect(next).toEqual(base);
	});

	it('set_instrumental toggles the flag', () => {
		const next = applyMusicDirectorOperations(base, [{ op: 'set_instrumental', instrumental: true }], caps);
		expect(next.instrumental).toBe(true);
	});

	it('set_settings partially merges only the given keys', () => {
		const next = applyMusicDirectorOperations(
			base,
			[{ op: 'set_settings', settings: { duration: 90, bpm: 92, key: 'C minor', time_signature: '4/4' } }],
			caps
		);
		expect(next.settings).toEqual({ duration: 90, seed: -1, bpm: 92, key: 'C minor', time_signature: '4/4' });
	});

	it('upsert_section is a no-op without a server-assigned string id', () => {
		expect(applyMusicDirectorOperations(base, [{ op: 'upsert_section', section: { kind: 'verse', lyrics: 'x' } }], caps)).toEqual(base);
	});

	it('upsert_section adds a new segment named after the wire kind', () => {
		const empty: MusicDirectorValue = { ...base, segments: [] };
		const next = applyMusicDirectorOperations(
			empty,
			[{ op: 'upsert_section', section: { id: 's1', kind: 'verse', lyrics: 'riding through the city' } }],
			caps
		);
		expect(next.segments).toHaveLength(1);
		expect(next.segments[0]).toMatchObject({ id: 's1', name: 'Verse', content: 'riding through the city', type: 'content', enabled: true });
	});

	it('upsert_section updates the matching segment and preserves the kind the op omits', () => {
		const value: MusicDirectorValue = { ...base, segments: [seg('Chorus', 'old lyrics', { id: 's1' })] };
		const next = applyMusicDirectorOperations(value, [{ op: 'upsert_section', section: { id: 's1', lyrics: 'new lyrics' } }], caps);
		expect(next.segments).toHaveLength(1);
		expect(next.segments[0]).toMatchObject({ id: 's1', name: 'Chorus', content: 'new lyrics' });
	});

	it('remove_section filters by id', () => {
		const value: MusicDirectorValue = { ...base, segments: [seg('Verse', 'a', { id: 's1' }), seg('Chorus', 'b', { id: 's2' })] };
		const next = applyMusicDirectorOperations(value, [{ op: 'remove_section', id: 's1' }], caps);
		expect(next.segments.map((s) => s.id)).toEqual(['s2']);
	});

	it('reorder_sections reorders by the given id list', () => {
		const value: MusicDirectorValue = { ...base, segments: [seg('Verse', 'a', { id: 's1' }), seg('Chorus', 'b', { id: 's2' })] };
		const next = applyMusicDirectorOperations(value, [{ op: 'reorder_sections', ids: ['s2', 's1'] }], caps);
		expect(next.segments.map((s) => s.id)).toEqual(['s2', 's1']);
	});

	it('upsert_reference is a no-op without a server-assigned string id or valid media', () => {
		expect(applyMusicDirectorOperations(base, [{ op: 'upsert_reference', reference: { media: media('x.wav') } }], caps)).toEqual(base);
		expect(applyMusicDirectorOperations(base, [{ op: 'upsert_reference', reference: { id: 'r1' } }], caps)).toEqual(base);
	});

	it('upsert_reference adds a new reference', () => {
		const next = applyMusicDirectorOperations(base, [{ op: 'upsert_reference', reference: { id: 'r1', media: media('ref.wav') } }], caps);
		expect(next.references).toEqual([{ id: 'r1', media: media('ref.wav') }]);
	});

	it('remove_reference filters by id', () => {
		const value: MusicDirectorValue = { ...base, references: [{ id: 'r1', media: media('a.wav') }, { id: 'r2', media: media('b.wav') }] };
		const next = applyMusicDirectorOperations(value, [{ op: 'remove_reference', id: 'r1' }], caps);
		expect(next.references).toEqual([{ id: 'r2', media: media('b.wav') }]);
	});

	it('applies a sequence of operations in order', () => {
		const empty: MusicDirectorValue = { ...base, segments: [] };
		const next = applyMusicDirectorOperations(
			empty,
			[
				{ op: 'set_description', description: 'dreamy synthwave' },
				{ op: 'upsert_section', section: { id: 's1', kind: 'verse', lyrics: 'one' } },
				{ op: 'upsert_section', section: { id: 's2', kind: 'chorus', lyrics: 'two' } },
				{ op: 'reorder_sections', ids: ['s2', 's1'] }
			],
			caps
		);
		expect(next.description).toBe('dreamy synthwave');
		expect(next.segments.map((s) => s.id)).toEqual(['s2', 's1']);
	});
});
