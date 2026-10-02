// Layout (2/3 panes) and prompt-pane width are per-tab now (see `$lib/types/tabs`
// Tab.layoutMode / Tab.promptPanelWidth), persisted with the rest of the tab state.
// This module only keeps the shared type + sizing constants.
export type { GenerationLayoutMode } from '$lib/types/tabs';

// Width of the prompts pane in three-pane mode (px), user-resizable.
// Only a minimum is enforced (so the pane can't collapse to nothing);
// how much room to leave the media pane is the user's call.
export const PROMPT_PANEL_MIN_WIDTH = 300;
export const DEFAULT_PROMPT_PANEL_WIDTH = 420;

// Settings (form) pane: fixed width per viewport tier, not user-resizable.
// Tiers match Tailwind's xl/2xl breakpoints. Content width assumes
// DynamicForm's p-4 (16px each side = 32px).
export const SETTINGS_PANE_WIDTH = 380;
export const SETTINGS_PANE_WIDTH_XL = 420;
export const SETTINGS_PANE_WIDTH_2XL = 460;
export const SETTINGS_PANE_BREAKPOINT_XL = 1280;
export const SETTINGS_PANE_BREAKPOINT_2XL = 1536;
const SETTINGS_PANE_CONTENT_PADDING = 32;

export function settingsPaneWidth(viewportWidth: number): number {
	if (viewportWidth >= SETTINGS_PANE_BREAKPOINT_2XL) return SETTINGS_PANE_WIDTH_2XL;
	if (viewportWidth >= SETTINGS_PANE_BREAKPOINT_XL) return SETTINGS_PANE_WIDTH_XL;
	return SETTINGS_PANE_WIDTH;
}

export function settingsPaneContentWidth(viewportWidth: number): number {
	return settingsPaneWidth(viewportWidth) - SETTINGS_PANE_CONTENT_PADDING;
}

export const PROMPT_PANEL_FOLDED_MIN_WIDTH = 448;
export const PROMPT_PANEL_FOLDED_MAX_WIDTH = 896;
export const PROMPT_PANEL_FOLDED_SHARE = 0.42;

export function foldedPromptPanelWidth(availableWidth: number): number {
	const share = availableWidth * PROMPT_PANEL_FOLDED_SHARE;
	return Math.round(
		Math.min(PROMPT_PANEL_FOLDED_MAX_WIDTH, Math.max(PROMPT_PANEL_FOLDED_MIN_WIDTH, share))
	);
}

// Video Director auto-widen: while the Director is active, the prompts pane
// (which hosts the console) grows to the widest it can be; on deactivation it
// returns to whatever the user had it at before. Both directions are pure
// reducers — `GenerationPanels.svelte` supplies the current width state (the
// tab's `promptPanelWidth`/`promptPanelWidthBeforeDirector`) and, for the
// widen direction, the pane's current maximum (from its own
// `promptWidthForClientX(panelRight)`) — and applies the returned patch via
// `tabsStore.updateTab`.
export interface PromptPanelWidthState {
	promptPanelWidth: number;
	promptPanelWidthBeforeDirector?: number;
}

/**
 * Video Director activation: widen the prompts pane to `maxWidth`, stashing
 * the prior width so it can be restored later. Returns `null` (no patch) when
 * the pane is already widened — `promptPanelWidthBeforeDirector` is the guard
 * that makes this idempotent across an activation, so a manual drag partway
 * through is never overwritten by a later call — or when there is nothing to
 * widen to (`maxWidth` no bigger than the current width).
 */
export function widenPromptPanelForDirector(
	state: PromptPanelWidthState,
	maxWidth: number
): Partial<PromptPanelWidthState> | null {
	if (state.promptPanelWidthBeforeDirector != null) return null;
	if (maxWidth <= state.promptPanelWidth) return null;
	return {
		promptPanelWidthBeforeDirector: state.promptPanelWidth,
		promptPanelWidth: maxWidth
	};
}

/**
 * Video Director deactivation: restore the prompts pane to the width stashed
 * by `widenPromptPanelForDirector` and clear the stash. Returns `null` (no
 * patch) when there is nothing stashed — the pane was never widened.
 */
export function restorePromptPanelFromDirector(
	state: PromptPanelWidthState
): Partial<PromptPanelWidthState> | null {
	if (state.promptPanelWidthBeforeDirector == null) return null;
	return {
		promptPanelWidth: state.promptPanelWidthBeforeDirector,
		promptPanelWidthBeforeDirector: undefined
	};
}

export const PROMPT_PANE_READABLE_MIN_WIDTH = 640;
export const WORKBENCH_COMFORT_WIDTH = 480;
export const WORKBENCH_FLOOR_WIDTH = 320;
export const FORM_RAIL_WIDTH = 12;
export const PANE_HANDLE_WIDTH = 4;
export const FORM_MAX_VIEWPORT_SHARE = 0.45;

export interface ThreePaneInput {
	panelsWidth: number;
	viewportWidth: number;
	formWidth: number;
	layoutMode: 'two' | 'three';
	promptless: boolean;
	leftPanelCollapsed: boolean;
	formUnfoldedByUser: boolean;
	workbenchCollapsed: boolean;
	promptPanelWidth: number;
	promptPanelWidthFolded?: number;
}

export interface ThreePaneLayout {
	formFolded: boolean;
	autoFolded: boolean;
	canAutoFold: boolean;
	promptMinWidth: number;
	promptWidth: number;
	workbenchMinWidth: number;
}

export function resolveThreePaneLayout(input: ThreePaneInput): ThreePaneLayout {
	const threePane = input.layoutMode === 'three' && !input.promptless;
	const formOpenWidth = Math.min(input.formWidth, input.viewportWidth * FORM_MAX_VIEWPORT_SHARE);
	const workbenchRail = input.workbenchCollapsed ? 0 : PANE_HANDLE_WIDTH;
	const workbenchNeed = input.workbenchCollapsed ? FORM_RAIL_WIDTH : WORKBENCH_COMFORT_WIDTH;
	const roomWithFormOpen =
		input.panelsWidth - formOpenWidth - FORM_RAIL_WIDTH - workbenchRail - workbenchNeed;
	const canAutoFold =
		threePane &&
		input.panelsWidth > 0 &&
		!input.leftPanelCollapsed &&
		roomWithFormOpen < PROMPT_PANE_READABLE_MIN_WIDTH;
	const autoFolded = canAutoFold && !input.formUnfoldedByUser;
	const formFolded = input.leftPanelCollapsed || autoFolded;
	const promptMinWidth = threePane ? PROMPT_PANE_READABLE_MIN_WIDTH : PROMPT_PANEL_MIN_WIDTH;

	const stored = formFolded
		? (input.promptPanelWidthFolded ?? foldedPromptPanelWidth(input.panelsWidth))
		: input.promptPanelWidth;
	const formSpace = formFolded ? FORM_RAIL_WIDTH : formOpenWidth + FORM_RAIL_WIDTH;
	const ceiling = input.panelsWidth - formSpace - workbenchRail - WORKBENCH_FLOOR_WIDTH;
	const promptWidth = Math.round(Math.max(promptMinWidth, Math.min(stored, ceiling)));
	const squeezed = input.panelsWidth - formSpace - workbenchRail - promptWidth < WORKBENCH_FLOOR_WIDTH;

	return {
		formFolded,
		autoFolded,
		canAutoFold,
		promptMinWidth,
		promptWidth,
		workbenchMinWidth: squeezed ? 0 : WORKBENCH_FLOOR_WIDTH
	};
}
