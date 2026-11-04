import { isActive } from './runs';
import { tileKey } from './tiles';
import type { OutputEntry, Run, RunCheckpoint } from './types';

export interface Lane {
	edgeId: string;
	target: string;
	label: string;
	letter: string;
	open: boolean;
	sent: boolean;
	automatic: boolean;
	selected: string[];
	eligible: string[];
	linked: string[];
	revision: number | undefined;
	error: string | null;
	partner?: string;
	partnerTarget?: string;
}

export interface Choice {
	lanes: Lane[];
	active: string | null;
	choosing: boolean;
}

export interface NodeChoiceOptions {
	drafts?: Record<string, string[] | undefined>;
	active?: string | null;
	labelFor?: (id: string) => string;
	readOnly?: boolean;
}

export interface TileTick {
	checked: boolean;
	locked: boolean;
	label: string;
}

export interface TileLetter {
	letter: string;
	label: string;
	active: boolean;
}

export interface ViewerSendTo {
	rows: Array<{ id: string; label: string; locked: boolean }>;
	isSelected: (rowId: string, entry: OutputEntry) => boolean;
	isEligible: (rowId: string, entry: OutputEntry) => boolean;
	onToggle: ((rowId: string, entry: OutputEntry, checked: boolean) => void) | undefined;
}

export function laneSelection(checkpoint: RunCheckpoint | null | undefined, draft?: string[] | null): string[] {
	if (!checkpoint) return [];
	if (checkpoint.confirmed !== null && checkpoint.confirmed !== undefined) return checkpoint.confirmed;
	return draft ?? checkpoint.draft ?? [];
}

export function laneLetters(labels: Array<string | null | undefined>): string[] {
	const firsts = labels.map((label) => (label || '?').trim().charAt(0).toUpperCase() || '?');
	return labels.map((label, index) => {
		const clash = firsts.some((letter, other) => other !== index && letter === firsts[index]);
		if (!clash) return firsts[index];
		const trimmed = (label || '?').trim();
		return (trimmed.charAt(0).toUpperCase() + (trimmed.charAt(1) || '')).trim() || '?';
	});
}

export function nodeChoice(run: Run | null | undefined, nodeId: string, { drafts = {}, active = null, labelFor = (id) => id, readOnly = false }: NodeChoiceOptions = {}): Choice | null {
	if (!run) return null;
	const checkpoints = run.checkpoints ?? {};
	const edges = (run.graph_edges ?? []).filter((edge) => edge.source === nodeId && checkpoints[edge.id]);
	if (edges.length === 0) return null;
	const live = isActive(run) && !run.cancel_requested_at && !readOnly;
	const labels = edges.map((edge) => labelFor(edge.target));
	const letters = laneLetters(labels);
	const lanes: Lane[] = edges.map((edge, index) => {
		const checkpoint = checkpoints[edge.id];
		const eligible = (checkpoint.eligible || []).map(tileKey);
		const sent = checkpoint.confirmed !== null && checkpoint.confirmed !== undefined;
		const selected = laneSelection(checkpoint, drafts[edge.id]);
		return {
			edgeId: edge.id,
			target: edge.target,
			label: labels[index],
			letter: letters[index],
			open: !sent && live && eligible.length > 0,
			sent,
			automatic: checkpoint.source === 'automatic',
			selected,
			eligible,
			linked: checkpoint.linked ?? [],
			revision: checkpoint.revision,
			error: checkpoint.error ?? null
		};
	});
	const firstOpen = lanes.find((lane) => lane.open);
	const activeLane = lanes.find((lane) => lane.edgeId === active) ?? firstOpen ?? null;
	return { lanes, active: activeLane?.edgeId ?? null, choosing: !!firstOpen };
}

export function tileChoice(choice: Choice | null | undefined, key: string): { tick: TileTick | null; letters: TileLetter[] } {
	if (!choice) return { tick: null, letters: [] };
	const lane = choice.lanes.find((item) => item.edgeId === choice.active);
	const tick =
		lane && lane.eligible.includes(key)
			? { checked: lane.selected.includes(key), locked: !lane.open, label: lane.label }
			: null;
	const holding = choice.lanes.filter((item) => item.selected.includes(key) && (item.open || item.sent));
	const showLetters = choice.choosing || holding.filter((item) => item.sent).length > 1;
	const letters = showLetters
		? holding.map((item) => ({ letter: item.letter, label: item.label, active: item.edgeId === choice.active }))
		: [];
	return { tick, letters };
}

export function toggled(selected: Iterable<string>, key: string, eligible: string[]): string[] {
	const set = new Set(selected);
	if (set.has(key)) set.delete(key);
	else set.add(key);
	return eligible.filter((item) => set.has(item));
}

export function laneTakes(lane: Pick<Lane, 'eligible'> | null | undefined, key: string): boolean {
	return !!lane && lane.eligible.includes(key);
}

export function viewerSendTo(choice: Choice | null | undefined, onToggle?: ViewerSendTo['onToggle']): ViewerSendTo | null {
	if (!choice?.choosing) return null;
	const lanes = choice.lanes.filter((lane) => lane.open || lane.sent);
	const laneOf = (rowId: string) => lanes.find((lane) => lane.edgeId === rowId);
	return {
		rows: lanes.map((lane) => ({ id: lane.edgeId, label: lane.label, locked: !lane.open })),
		isSelected: (rowId, entry) => !!laneOf(rowId)?.selected.includes(tileKey(entry)),
		isEligible: (rowId, entry) => laneTakes(laneOf(rowId), tileKey(entry)),
		onToggle
	};
}
