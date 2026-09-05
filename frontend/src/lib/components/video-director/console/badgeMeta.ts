// Display metadata for the console's dependency badge and join-kind icons --
// pure lookup tables, shared between ShotRow, ShotCard and JoinConnector so
// the icon/color-per-state mapping (console.html / shot-states.html) lives
// in one place. Copy (labels) always comes from the consoleModel/*, never
// hardcoded here -- only which icon and which of the two chip tones a given
// enum value gets.
import type { ConsoleIconName } from './ConsoleIcon.svelte';

export type ConsoleBadge = 'independent' | 'needs-previous' | 'input-ready' | 'stale' | 'continuous' | 'unverified';
export type ConsoleJoinKind = 'cut' | 'continue' | 'native' | 'missing';

export interface BadgeMeta {
	icon: ConsoleIconName;
	/** `.dep-badge` tone class: neutral (fg-subtle), ready (success) or warning. */
	tone: 'neutral' | 'ready' | 'warning';
}

const BADGE_META: Record<ConsoleBadge, BadgeMeta> = {
	independent: { icon: 'zap', tone: 'neutral' },
	'needs-previous': { icon: 'corner-down-right', tone: 'warning' },
	'input-ready': { icon: 'link', tone: 'ready' },
	stale: { icon: 'refresh-cw', tone: 'warning' },
	continuous: { icon: 'git-merge', tone: 'neutral' },
	unverified: { icon: 'alert-triangle', tone: 'warning' }
};

export function badgeMeta(badge: ConsoleBadge): BadgeMeta {
	return BADGE_META[badge];
}

export const BADGE_TONE_CLASS: Record<BadgeMeta['tone'], string> = {
	neutral: 'text-fg-subtle',
	ready: 'text-success',
	warning: 'text-warning'
};

export interface JoinKindMeta {
	icon: ConsoleIconName;
	/** Icon color for the non-warning join-block variants (`.join-kind` text stays fg-muted). */
	iconClass: string;
	/** `.join-chip` tone when this kind pairs with a chip control. */
	chipTone: 'neutral' | 'ready';
}

const JOIN_KIND_META: Record<ConsoleJoinKind, JoinKindMeta> = {
	cut: { icon: 'scissors', iconClass: 'text-fg-subtle', chipTone: 'neutral' },
	continue: { icon: 'link', iconClass: 'text-success', chipTone: 'ready' },
	native: { icon: 'git-merge', iconClass: 'text-success', chipTone: 'ready' },
	missing: { icon: 'alert-triangle', iconClass: '', chipTone: 'neutral' }
};

export function joinKindMeta(kind: ConsoleJoinKind): JoinKindMeta {
	return JOIN_KIND_META[kind];
}
