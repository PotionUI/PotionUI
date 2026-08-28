/**
 * Pure logic for ChatContextStrip.svelte — the strip docked above the
 * composer that states which tab the chat is reading (see UnifiedAIChat's
 * `contextTab` = pinned tab if pinned, else the active tab). Kept separate
 * from the component so the state machine (following / pinned-active /
 * pinned-mismatch) is unit-testable without mounting Svelte.
 */

export type ContextStripState = 'following' | 'pinned-active' | 'pinned-mismatch';

export interface ContextStripTabInfo {
	id: string;
	name: string;
	selectedPreset: string | null;
	selectedMode: string | null;
	formData: Record<string, unknown> | null | undefined;
}

export interface ContextStripModel {
	state: ContextStripState;
	/** The tab the strip is reporting on — the pinned tab when pinned, else the active tab. */
	tabName: string;
	/** e.g. "Krea-2 Turbo · txt2img"; null while the preset name hasn't resolved yet or none is selected. */
	presetLabel: string | null;
	/** "1216×832"; null when the tab's form has no usable width/height yet. */
	dims: string | null;
	steps: number | null;
	/** Only set for 'pinned-mismatch': the tab actually open in Generate right now. */
	activeTabName: string | null;
}

function isPositiveFiniteNumber(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

function dimsFromFormData(formData: Record<string, unknown> | null | undefined): string | null {
	const width = formData?.width;
	const height = formData?.height;
	if (isPositiveFiniteNumber(width) && isPositiveFiniteNumber(height)) {
		return `${width}×${height}`;
	}
	return null;
}

function stepsFromFormData(formData: Record<string, unknown> | null | undefined): number | null {
	const steps = formData?.steps;
	return isPositiveFiniteNumber(steps) ? steps : null;
}

/** "Krea-2 Turbo · txt2img", "Krea-2 Turbo" (no mode), or null (no preset name yet). */
export function presetLabelFor(
	presetName: string | null,
	selectedMode: string | null
): string | null {
	if (!presetName) return null;
	return selectedMode ? `${presetName} · ${selectedMode}` : presetName;
}

function describeTab(
	tab: ContextStripTabInfo,
	presetName: (presetId: string) => string | null
): Pick<ContextStripModel, 'tabName' | 'presetLabel' | 'dims' | 'steps'> {
	return {
		tabName: tab.name,
		presetLabel: presetLabelFor(
			tab.selectedPreset ? presetName(tab.selectedPreset) : null,
			tab.selectedMode
		),
		dims: dimsFromFormData(tab.formData),
		steps: stepsFromFormData(tab.formData)
	};
}

/**
 * Derives what the strip should show. `pinnedTab` must be the tab resolved
 * from `pinnedTabId` (or null/undefined if it no longer exists) — the caller
 * (UnifiedAIChat) already clears a stale `pinnedTabId` reactively, but this
 * degrades to 'following' defensively rather than throwing if it sees one
 * anyway. Returns null only when there is no tab at all to report on.
 */
export function deriveContextStripModel(params: {
	activeTab: ContextStripTabInfo | null | undefined;
	pinnedTab: ContextStripTabInfo | null | undefined;
	pinnedTabId: string | null;
	presetName: (presetId: string) => string | null;
}): ContextStripModel | null {
	const { activeTab, pinnedTab, pinnedTabId, presetName } = params;

	if (!pinnedTabId || !pinnedTab) {
		if (!activeTab) return null;
		return { state: 'following', activeTabName: null, ...describeTab(activeTab, presetName) };
	}

	const mismatch = !activeTab || activeTab.id !== pinnedTab.id;
	return {
		state: mismatch ? 'pinned-mismatch' : 'pinned-active',
		activeTabName: mismatch ? (activeTab?.name ?? null) : null,
		...describeTab(pinnedTab, presetName)
	};
}

export interface ContextSwitchDivider {
	tabName: string;
	presetLabel: string | null;
	dims: string | null;
}

/**
 * The tab-switch transcript divider ("Switched to X · preset · dims"),
 * derived the same way as the strip itself. Fires only when FOLLOWING
 * (pinning is a deliberate override, not a "switch" to announce), only when
 * the tab genuinely changed since the last check, and only when there's an
 * existing message to anchor the divider after. Returns null when none of
 * that holds — the caller still updates its own "last seen tab" tracking on
 * every call regardless of the return value.
 */
export function deriveTabSwitchDivider(params: {
	previousTabId: string | null;
	activeTab: ContextStripTabInfo | null | undefined;
	pinnedTabId: string | null;
	hasMessages: boolean;
	presetName: (presetId: string) => string | null;
}): ContextSwitchDivider | null {
	const { previousTabId, activeTab, pinnedTabId, hasMessages, presetName } = params;
	if (pinnedTabId || !activeTab || !hasMessages) return null;
	if (previousTabId === null || previousTabId === activeTab.id) return null;
	const { tabName, presetLabel, dims } = describeTab(activeTab, presetName);
	return { tabName, presetLabel, dims };
}
