import type { Session, SessionVersionSummary } from '$lib/types/api';
import { parseServerDate } from '$lib/utils/relativeTime';

export interface Group<T> {
	label: string;
	items: T[];
}

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
	return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function dayDiff(date: Date, now: Date): number {
	return Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
}

export function filterSessions(sessions: Session[], query: string, excludeId = ''): Session[] {
	const needle = query.trim().toLowerCase();
	return sessions.filter(
		(session) => session.id !== excludeId && (!needle || session.name.toLowerCase().includes(needle))
	);
}

export function splitPinned(sessions: Session[]): { pinned: Session[]; rest: Session[] } {
	return {
		pinned: sessions.filter((session) => session.pinned),
		rest: sessions.filter((session) => !session.pinned)
	};
}

function updatedTime(session: Session): number {
	return parseServerDate(session.updated_at)?.getTime() ?? 0;
}

export function applyPin(sessions: Session[], sessionId: string, pinned: boolean): Session[] {
	const target = sessions.find((session) => session.id === sessionId);
	if (!target) return sessions;
	const others = sessions.filter((session) => session.id !== sessionId);
	const updated = { ...target, pinned };
	const keptPinned = others.filter((session) => session.pinned);
	const rest = others.filter((session) => !session.pinned);
	if (pinned) return [updated, ...keptPinned, ...rest];
	return [...keptPinned, ...[...rest, updated].sort((a, b) => updatedTime(b) - updatedTime(a))];
}

function sessionBucket(session: Session, now: Date): string {
	const updated = parseServerDate(session.updated_at);
	if (!updated) return 'Earlier';
	const diff = dayDiff(updated, now);
	if (diff <= 0) return 'Today';
	if (diff === 1) return 'Yesterday';
	if (diff < 7) return 'This week';
	if (diff < 31) return 'This month';
	return 'Earlier';
}

const BUCKET_ORDER = ['Today', 'Yesterday', 'This week', 'This month', 'Earlier'];

export function groupSessions(sessions: Session[], now: Date = new Date()): Group<Session>[] {
	const sorted = [...sessions].sort((a, b) => updatedTime(b) - updatedTime(a));
	const buckets = new Map<string, Session[]>();
	for (const session of sorted) {
		const label = sessionBucket(session, now);
		const bucket = buckets.get(label);
		if (bucket) bucket.push(session);
		else buckets.set(label, [session]);
	}
	return BUCKET_ORDER.filter((label) => buckets.has(label)).map((label) => ({
		label,
		items: buckets.get(label)!
	}));
}

export function dayLabel(iso: string, now: Date = new Date()): string {
	const date = parseServerDate(iso);
	if (!date) return 'Unknown';
	const diff = dayDiff(date, now);
	if (diff <= 0) return 'Today';
	if (diff === 1) return 'Yesterday';
	return date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

export function groupVersionsByDay(
	versions: SessionVersionSummary[],
	now: Date = new Date()
): Group<SessionVersionSummary>[] {
	const groups: Group<SessionVersionSummary>[] = [];
	for (const version of versions) {
		const label = dayLabel(version.created_at, now);
		const last = groups[groups.length - 1];
		if (last && last.label === label) last.items.push(version);
		else groups.push({ label, items: [version] });
	}
	return groups;
}

export function versionHeadline(version: SessionVersionSummary): string {
	return version.prompt_preview?.trim() || version.summary;
}

export const CHANGE_LABELS_SHOWN = 3;

export type FieldLabels = Record<string, string>;

export interface VersionChangeSummary {
	shown: string[];
	more: number;
	all: string[];
}

export function humanizeKey(key: string): string {
	return key.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
}

export function versionChanges(
	version: SessionVersionSummary,
	fieldLabels: FieldLabels = {}
): VersionChangeSummary | null {
	const labels = [...(version.changes ?? [])];
	for (const key of version.changed_fields ?? []) {
		const label = fieldLabels[key] || humanizeKey(key);
		if (label && !labels.includes(label)) labels.push(label);
	}
	if (labels.length === 0) return null;
	return {
		shown: labels.slice(0, CHANGE_LABELS_SHOWN),
		more: Math.max(0, labels.length - CHANGE_LABELS_SHOWN),
		all: labels
	};
}

export type DiscardAction =
	| { kind: 'load' }
	| { kind: 'restore'; versionNumber: number }
	| { kind: 'new' };

export function discardConsequence(action: DiscardAction): string {
	if (action.kind === 'restore') return `Restoring version v${action.versionNumber} replaces them.`;
	if (action.kind === 'new') return 'Starting a new session clears them.';
	return 'Loading another session replaces them.';
}

export function fullDateTime(iso: string | undefined): string {
	const date = parseServerDate(iso);
	return date ? date.toLocaleString() : '';
}
