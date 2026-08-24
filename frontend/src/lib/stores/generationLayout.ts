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
