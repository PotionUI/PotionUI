import type { MediaRef } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';

// Mirrors src/features/music_director/normalize.py's `_MODES`/`_SECTION_MODES`/
// `_SECTION_KINDS` exactly (see docs/music-director.md). Unlike Video
// Director's `director` mode, every Music Director mode's document shape is
// fixed by its name -- there is no capability-driven structural fork.
export type MusicMode = 't2m' | 'song' | 'style' | 'extend' | 'repaint' | 'director';

export const MUSIC_MODE_ORDER: MusicMode[] = ['t2m', 'song', 'style', 'director', 'extend', 'repaint'];

export type SectionKind =
	| 'intro'
	| 'verse'
	| 'pre_chorus'
	| 'chorus'
	| 'post_chorus'
	| 'bridge'
	| 'instrumental'
	| 'solo'
	| 'outro';

export const SECTION_KINDS: SectionKind[] = [
	'intro',
	'verse',
	'pre_chorus',
	'chorus',
	'post_chorus',
	'bridge',
	'instrumental',
	'solo',
	'outro'
];

// ─── Capabilities (parsed `vars.music_director`) ────────────────────────────

// One capability block per composition mode. Fields that don't apply to a
// given mode (e.g. `maxSections` on `t2m`) are simply never read -- same
// approach as DirectorModeCapability in videoDirector.ts, one shape for every
// mode rather than a union per mode.
export interface MusicModeCapability {
	/** `style` only -- metadata, not an enforced bound (see docs/music-director.md `references`). */
	maxReferenceSeconds: number | null;
	/** `director` only -- defaults to 12, mirroring normalize.py's `mode_caps.get("max_sections", 12)`. */
	maxSections: number;
	/** `director` only. */
	perSectionPrompts: boolean;
	/** `director` only -- gates the per-section `duration_hint` input; a model whose
	 * pipeline never reads a per-section duration (most of them -- it's UI-only, driving
	 * the rail's block widths) should not offer a knob that does nothing. */
	sectionDurationHints: boolean;
	/** `director` only -- absent capability key means null (no reference pool at all for this mode). */
	references: 'whole' | 'per_section' | null;
	/** `director` only, frontend/pipe-facing -- the normalizer never reads this. */
	compile: 'single_shot' | null;
	/** Mirrors normalize.py's `mode_caps.get("description_required")`: this mode's
	 * generator has no fallback conditioning signal (no audio source), so an empty
	 * `description` is rejected instead of the framework's normal "valid but
	 * useless" default. */
	descriptionRequired: boolean;
}

export interface MusicSettingsCapability {
	bpm: boolean;
	key: boolean;
	timeSignature: boolean;
}

export interface MusicLimits {
	defaultDuration: number;
	maxDuration: number | null;
	/** Pipe-facing metadata; the normalizer doesn't enforce it. */
	sampleRate: number | null;
	stereo: boolean;
}

export interface MusicDirectorCapabilities {
	/** null = every preset mode is eligible for the Music Director UI. */
	presetModes: string[] | null;
	modes: Partial<Record<MusicMode, MusicModeCapability>>;
	/** MUSIC_MODE_ORDER filtered to declared modes. */
	enabledModes: MusicMode[];
	settings: MusicSettingsCapability;
	limits: MusicLimits;
	/** When true, the preset's dynamic form owns duration/instrumental/style
	 * description as plain fields (`form.description`/`.duration`/
	 * `.instrumental`) -- the editor renders none of the settings row or the
	 * style description textarea, only the Song structure segment editor and
	 * whatever reference/extend/repaint wells this mode set declares.
	 * Defaults false so an existing preset's editor is unchanged. */
	formOwnsSettings: boolean;
}

// ─── Editor value ────────────────────────────────────────────────────────────

export interface MusicReferenceItem {
	id: string;
	media: MediaRef;
}

export interface MusicDirectorSettings {
	duration: number;
	seed: number;
	bpm: number | null;
	key: string | null;
	time_signature: string | null;
}

export interface MusicDirectorValue {
	schema_version: 1;
	/** Kept coherent with the document's actual structure on every edit
	 * (`deriveMusicDirectorMode`, mirroring Video Director's `deriveDirectorMode`)
	 * -- the editor has no mode switch; this field exists for the wire contract
	 * and chat tooling that still key off it, not because the user ever sets it
	 * directly. */
	mode: MusicMode;
	description: string;
	/** The editor's "Instrumental (no vocals)" toggle -- client-side only, never
	 * itself sent on the wire. Feeds `deriveMusicDirectorMode`: ON derives `t2m`
	 * when the preset declares it (this family's checkpoint has no separate
	 * instrumental preset mode any more -- the pipeline composes the literal
	 * "[instrumental]" lyrics whenever the submitted mode is `t2m`). */
	instrumental: boolean;
	/** `song`/`director` only; empty for every other mode. One segment = one
	 * song section: `type: 'content'`'s `name` canonicalizes to that section's
	 * `SectionKind` (`canonicalizeSectionKind`), `content` is its lyrics.
	 * `type: 'break'` segments carry no lyric content and are skipped at the
	 * wire (see `wireSection` in utils/musicDirector.ts). Edited by the same
	 * shared `SegmentedPromptEditor` every other segment list in the app
	 * uses -- Music Director owns no bespoke section-editing UI. */
	segments: Segment[];
	/** Inline reference-audio pool -- addressed by id from a `director`
	 * per-section selection, or used wholesale otherwise. Gated by
	 * `musicReferencesGate`. */
	references: MusicReferenceItem[];
	/** `extend` only. */
	extend_source: { media: MediaRef } | null;
	/** `repaint` only -- `source` is null until the user picks a track, `start`/
	 * `end` always carry a value so the range inputs stay controlled. */
	repaint: { source: { media: MediaRef } | null; start: number; end: number };
	settings: MusicDirectorSettings;
}

// ─── Wire document (form_data.music_director) ───────────────────────────────

export interface MusicDirectorWireSection {
	id: string;
	kind: SectionKind;
	lyrics: string;
	/** Only ever emitted where `director.per_section_prompts`/`.references ==
	 * 'per_section'` are declared -- dead capability today (the segment
	 * editor has no UI to set either), kept in the wire contract for a
	 * future frontend surface. `buildMusicDirectorSubmission` never emits
	 * these fields itself. */
	style_hint?: string;
	duration_hint?: number;
	references?: string[];
}

export interface MusicDirectorWireDoc {
	schema_version: 1;
	mode: MusicMode;
	description: string;
	/** `song` mode may submit a single section object as shorthand (see
	 * docs/music-director.md `sections`) -- `buildMusicDirectorSubmission`
	 * emits that shape when the song has exactly one section. */
	sections?: MusicDirectorWireSection[] | MusicDirectorWireSection;
	/** Carried opaquely for round-trip fidelity -- the backend normalizer
	 * never reads this, only `sections[].lyrics`/`.kind` (see
	 * `MusicDirectorValue.segments`). */
	segments?: Segment[];
	references?: { id: string; media: MediaRef }[];
	extend_source?: { media: MediaRef } | null;
	repaint?: { source: { media: MediaRef }; start: number; end: number } | null;
	settings: {
		duration: number;
		seed: number;
		bpm?: number;
		key?: string;
		time_signature?: string;
	};
}
