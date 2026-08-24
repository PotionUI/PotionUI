import type { SectionKind } from '$lib/types/musicDirector';

// Display-only mapping (label + quick-add/chip color) for a section kind --
// never consulted by validation/submission logic beyond
// `canonicalizeSectionKind` matching a segment's `name` back against these
// exact labels (case-insensitively).
const LABELS: Record<SectionKind, string> = {
	intro: 'Intro',
	verse: 'Verse',
	pre_chorus: 'Pre-Chorus',
	chorus: 'Chorus',
	post_chorus: 'Post-Chorus',
	bridge: 'Bridge',
	instrumental: 'Instrumental',
	solo: 'Solo',
	outro: 'Outro'
};

// `rgb(var(--viz-N))` rather than a hex/class so it stays theme-aware when
// written straight into a Segment's `color` (consumed as a raw CSS
// background-color by PromptSegment.svelte) -- intro/outro/instrumental read
// as structural (subtle), verse/pre-chorus/chorus/bridge each get a distinct
// hue, mirroring the old Arrangement Rail's block colors.
const CSS_COLORS: Record<SectionKind, string> = {
	intro: 'rgb(var(--fg-subtle))',
	verse: 'rgb(var(--viz-5))',
	pre_chorus: 'rgb(var(--viz-3))',
	chorus: 'rgb(var(--viz-8))',
	post_chorus: 'rgb(var(--viz-2))',
	bridge: 'rgb(var(--viz-7))',
	instrumental: 'rgb(var(--fg-subtle))',
	solo: 'rgb(var(--viz-6))',
	outro: 'rgb(var(--fg-subtle))'
};

export function sectionKindLabel(kind: SectionKind): string {
	return LABELS[kind];
}

export function sectionKindColor(kind: SectionKind): string {
	return CSS_COLORS[kind];
}
