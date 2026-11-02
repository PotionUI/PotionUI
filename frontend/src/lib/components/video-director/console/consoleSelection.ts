import type { ShotRailModel } from './shotRailModel';

// Shot Console selection -- COMPONENT state (owned by ShotConsole.svelte),
// never a module-scope store. `railSelection.ts` (the Stage & Rail rework's
// own selection store) is being deleted for exactly this reason: a
// module-scope store survives an SPA user switch (see the project memory
// trap on this). A rail object is always scoped to the shot that owns it --
// unlike the old film-wide `RailSelectionId`, a console selection carries its
// `shotId` alongside the object kind/id so a click on shot 2's keyframe never
// reads as a stale selection while shot 1 is expanded.

export type ConsoleSelectionKind = 'keyframe' | 'beat' | 'audio';

export type ConsoleSelection = { shotId: string; kind: ConsoleSelectionKind; id: string } | null;

/** True when `selection` points at exactly this (shotId, kind, id) triple --
 * the equality check every rail mark's "am I selected" class reduces to. */
export function consoleSelectionMatches(selection: ConsoleSelection, shotId: string, kind: ConsoleSelectionKind, id: string): boolean {
	return selection != null && selection.shotId === shotId && selection.kind === kind && selection.id === id;
}

/** True when `selection` belongs to `shotId` at all -- used to clear a stale
 * selection when the active shot changes (a selection never survives a jump
 * to a different shot's rail). */
export function consoleSelectionBelongsToShot(selection: ConsoleSelection, shotId: string): boolean {
	return selection != null && selection.shotId === shotId;
}

function selectionOnRail(selection: NonNullable<ConsoleSelection>, rail: ShotRailModel, segmentRouting: boolean): boolean {
	if (selection.kind === 'beat') {
		return (segmentRouting && selection.id === selection.shotId) || !!rail.lanes.prompt?.beats.some((b) => b.id === selection.id);
	}
	if (selection.kind === 'keyframe') return !!rail.lanes.keyframes?.marks.some((m) => m.id === selection.id);
	return !!rail.lanes.audio?.clips.some((c) => c.id === selection.id);
}

export function resolveConsoleSelection(
	selection: ConsoleSelection,
	shotId: string,
	rail: ShotRailModel,
	segmentRouting: boolean
): ConsoleSelection {
	if (selection && selection.shotId === shotId && selectionOnRail(selection, rail, segmentRouting)) return selection;
	return segmentRouting ? { shotId, kind: 'beat', id: shotId } : null;
}
