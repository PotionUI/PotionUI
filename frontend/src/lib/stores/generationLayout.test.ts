import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
	widenPromptPanelForDirector,
	restorePromptPanelFromDirector,
	foldedPromptPanelWidth,
	resolveThreePaneLayout,
	toggleFormPane,
	setFormAutoFoldable,
	formFoldStates,
	PROMPT_PANE_READABLE_MIN_WIDTH,
	WORKBENCH_FLOOR_WIDTH,
	type ThreePaneInput,
	PROMPT_PANEL_FOLDED_MIN_WIDTH,
	PROMPT_PANEL_FOLDED_MAX_WIDTH
} from './generationLayout';

// Video Director auto-widen (PLAN.md §C W4): pure reducers only — the
// stashing/restoring of the prompts pane width around Director activation.
// DOM measurement and the effect that calls these live in
// `GenerationPanels.svelte`.

describe('widenPromptPanelForDirector', () => {
	it('stashes the current width and widens to the maximum', () => {
		const patch = widenPromptPanelForDirector({ promptPanelWidth: 420 }, 900);
		expect(patch).toEqual({ promptPanelWidthBeforeDirector: 420, promptPanelWidth: 900 });
	});

	it('is a no-op once already widened, even if the maximum changed', () => {
		const patch = widenPromptPanelForDirector(
			{ promptPanelWidth: 900, promptPanelWidthBeforeDirector: 420 },
			1200
		);
		expect(patch).toBeNull();
	});

	it('is a no-op when there is nothing to widen to', () => {
		const atMax = widenPromptPanelForDirector({ promptPanelWidth: 900 }, 900);
		const below = widenPromptPanelForDirector({ promptPanelWidth: 900 }, 800);
		expect(atMax).toBeNull();
		expect(below).toBeNull();
	});
});

describe('restorePromptPanelFromDirector', () => {
	it('restores the stashed width and clears the stash', () => {
		const patch = restorePromptPanelFromDirector({
			promptPanelWidth: 900,
			promptPanelWidthBeforeDirector: 420
		});
		expect(patch).toEqual({ promptPanelWidth: 420, promptPanelWidthBeforeDirector: undefined });
	});

	it('is a no-op when the pane was never widened', () => {
		const patch = restorePromptPanelFromDirector({ promptPanelWidth: 420 });
		expect(patch).toBeNull();
	});

	it('round-trips: widen then restore returns the original width', () => {
		const initial = { promptPanelWidth: 420 };
		const widened = { ...initial, ...widenPromptPanelForDirector(initial, 900) };
		const restored = { ...widened, ...restorePromptPanelFromDirector(widened) };
		expect(restored.promptPanelWidth).toBe(420);
		expect(restored.promptPanelWidthBeforeDirector).toBeUndefined();
	});

	// Regression: GenerationPanels.svelte originally drove the restore off an
	// edge computed from a local "was Director active last render" variable.
	// The component only mounts for the active tab, and a preset switch can
	// pass through a moment where the mode is briefly unset, unmounting and
	// remounting it — by the time it remounts with `videoDirectorActive`
	// already `false`, that local variable had re-initialized to `false` too,
	// so no edge was ever observed and the restore never fired (the pane
	// stayed stuck at its widened width). Because this reducer takes only the
	// tab's own PERSISTED state — nothing carried over from a prior render —
	// it restores correctly on a "cold" call with no history behind it,
	// which is what makes deriving the trigger from `promptPanelWidthBeforeDirector`
	// directly (rather than from any local previous-value bookkeeping)
	// remount-safe. See `GenerationPanels.svelte`'s own reactive block.
	it('restores correctly from a cold call with no memory of the prior activation', () => {
		// Simulates the state exactly as it would be read on first render
		// after a remount: only the persisted stash to go on.
		const stateAfterRemount = { promptPanelWidth: 1068, promptPanelWidthBeforeDirector: 420 };
		const patch = restorePromptPanelFromDirector(stateAfterRemount);
		expect(patch).toEqual({ promptPanelWidth: 420, promptPanelWidthBeforeDirector: undefined });
	});
});

describe('foldedPromptPanelWidth', () => {
	it('takes a 42% share at 1280px and 1440px', () => {
		expect(foldedPromptPanelWidth(1280)).toBe(538);
		expect(foldedPromptPanelWidth(1440)).toBe(605);
	});

	it('clamps to the minimum on a narrow row', () => {
		expect(foldedPromptPanelWidth(320)).toBe(PROMPT_PANEL_FOLDED_MIN_WIDTH);
		expect(foldedPromptPanelWidth(0)).toBe(PROMPT_PANEL_FOLDED_MIN_WIDTH);
	});

	it('clamps to the maximum on a wide row', () => {
		expect(foldedPromptPanelWidth(1920)).toBe(806);
		expect(foldedPromptPanelWidth(2560)).toBe(PROMPT_PANEL_FOLDED_MAX_WIDTH);
	});
});

const base: ThreePaneInput = {
	panelsWidth: 1480,
	viewportWidth: 1536,
	formWidth: 460,
	layoutMode: 'three',
	promptless: false,
	leftPanelCollapsed: false,
	formUnfoldedByUser: false,
	workbenchCollapsed: false,
	promptPanelWidth: 420
};

describe('resolveThreePaneLayout', () => {
	it('folds the form at a 1536 px viewport', () => {
		const out = resolveThreePaneLayout(base);
		expect(out.formFolded).toBe(true);
		expect(out.autoFolded).toBe(true);
		expect(out.promptWidth).toBeGreaterThanOrEqual(PROMPT_PANE_READABLE_MIN_WIDTH);
	});

	it('shows all three panes at 1920 px', () => {
		const out = resolveThreePaneLayout({ ...base, panelsWidth: 1864, viewportWidth: 1920 });
		expect(out.formFolded).toBe(false);
		expect(out.canAutoFold).toBe(false);
		expect(out.promptWidth).toBe(PROMPT_PANE_READABLE_MIN_WIDTH);
	});

	it('keeps a wider stored prompt width when there is room', () => {
		const out = resolveThreePaneLayout({
			...base,
			panelsWidth: 1864,
			viewportWidth: 1920,
			promptPanelWidth: 800
		});
		expect(out.promptWidth).toBe(800);
	});

	it('never auto-folds in two-pane layout', () => {
		const out = resolveThreePaneLayout({ ...base, layoutMode: 'two' });
		expect(out.formFolded).toBe(false);
		expect(out.canAutoFold).toBe(false);
	});

	it('never auto-folds a promptless mode', () => {
		expect(resolveThreePaneLayout({ ...base, promptless: true }).formFolded).toBe(false);
	});

	it('lets the user unfold the form and keeps prompts at the minimum while the workbench shrinks', () => {
		const out = resolveThreePaneLayout({
			...base,
			panelsWidth: 1200,
			viewportWidth: 1280,
			formWidth: 420,
			formUnfoldedByUser: true
		});
		expect(out.formFolded).toBe(false);
		expect(out.promptWidth).toBe(PROMPT_PANE_READABLE_MIN_WIDTH);
		expect(out.workbenchMinWidth).toBe(0);
	});

	it('keeps the workbench floor when the prompts pane leaves room', () => {
		const out = resolveThreePaneLayout({ ...base, panelsWidth: 1864, viewportWidth: 1920 });
		expect(out.workbenchMinWidth).toBe(WORKBENCH_FLOOR_WIDTH);
	});

	it('respects a form the user folded at any width', () => {
		const out = resolveThreePaneLayout({
			...base,
			panelsWidth: 1864,
			viewportWidth: 1920,
			leftPanelCollapsed: true
		});
		expect(out.formFolded).toBe(true);
		expect(out.autoFolded).toBe(false);
	});

	it('uses the folded prompt width while folded', () => {
		const out = resolveThreePaneLayout({ ...base, promptPanelWidthFolded: 700 });
		expect(out.promptWidth).toBe(700);
	});

	it('does not fold before the pane width is measured', () => {
		expect(resolveThreePaneLayout({ ...base, panelsWidth: 0 }).formFolded).toBe(false);
	});
});

describe('toggleFormPane', () => {
	beforeEach(() => {
		formFoldStates.set({});
	});

	it('unfolds an auto-folded form without touching the stored fold', () => {
		setFormAutoFoldable('t', true);
		expect(toggleFormPane('t', false)).toBeNull();
		expect(get(formFoldStates).t.unfolded).toBe(true);
	});

	it('folds the form back on the next toggle', () => {
		setFormAutoFoldable('t', true);
		toggleFormPane('t', false);
		expect(toggleFormPane('t', false)).toBeNull();
		expect(get(formFoldStates).t.unfolded).toBe(false);
	});

	it('flips the stored fold when the screen is wide', () => {
		setFormAutoFoldable('t', false);
		expect(toggleFormPane('t', false)).toEqual({ leftPanelCollapsed: true });
		expect(toggleFormPane('t', true)).toEqual({ leftPanelCollapsed: false });
	});

	it('forgets the unfold once the screen is wide again', () => {
		setFormAutoFoldable('t', true);
		toggleFormPane('t', false);
		setFormAutoFoldable('t', false);
		expect(get(formFoldStates).t.unfolded).toBe(false);
	});
});

describe('resolveThreePaneLayout freed form width', () => {
	const workbenchWidth = (input: ThreePaneInput, promptWidth: number, folded: boolean) => {
		const formOpen = Math.min(input.formWidth, input.viewportWidth * 0.45);
		const formSpace = folded ? 12 : formOpen + 12;
		return input.panelsWidth - formSpace - 4 - promptWidth;
	};

	it('gives the freed width to prompts at 1536 px and keeps the workbench width', () => {
		const open = resolveThreePaneLayout({ ...base, formUnfoldedByUser: true });
		const folded = resolveThreePaneLayout(base);
		expect(folded.formFolded).toBe(true);
		expect(folded.promptWidth).toBeGreaterThan(base.promptPanelWidth);
		expect(folded.promptWidth).toBeGreaterThan(open.promptWidth);
		const before = workbenchWidth(base, open.promptWidth, false);
		const after = workbenchWidth(base, folded.promptWidth, true);
		expect(Math.abs(after - before)).toBeLessThanOrEqual(2);
	});

	it('does the same at 1920 px with a user fold', () => {
		const wide = { ...base, panelsWidth: 1864, viewportWidth: 1920, promptPanelWidth: 700 };
		const open = resolveThreePaneLayout(wide);
		const folded = resolveThreePaneLayout({ ...wide, leftPanelCollapsed: true });
		expect(folded.promptWidth).toBeGreaterThan(open.promptWidth);
		const before = workbenchWidth(wide, open.promptWidth, false);
		const after = workbenchWidth(wide, folded.promptWidth, true);
		expect(Math.abs(after - before)).toBeLessThanOrEqual(2);
	});

	it('restores the previous split when unfolded', () => {
		const wide = { ...base, panelsWidth: 1864, viewportWidth: 1920, promptPanelWidth: 700 };
		const folded = resolveThreePaneLayout({ ...wide, leftPanelCollapsed: true });
		const unfolded = resolveThreePaneLayout({ ...wide, leftPanelCollapsed: false });
		expect(folded.promptWidth).not.toBe(unfolded.promptWidth);
		expect(unfolded.promptWidth).toBe(700);
	});
});

describe('resolveThreePaneLayout with a folded workbench', () => {
	const fill = (panels: number, formSpace: number) => panels - formSpace - 32;

	it('fills the rest with prompts when only the workbench is folded', () => {
		const open = resolveThreePaneLayout({ ...base, panelsWidth: 1864, viewportWidth: 1920 });
		const out = resolveThreePaneLayout({
			...base,
			panelsWidth: 1864,
			viewportWidth: 1920,
			workbenchCollapsed: true
		});
		expect(out.formFolded).toBe(false);
		expect(out.promptWidth).toBe(fill(1864, 460 + 12));
		expect(out.promptWidth).toBeGreaterThan(open.promptWidth);
	});

	it('keeps the auto-folded form folded when the workbench folds', () => {
		const out = resolveThreePaneLayout({ ...base, workbenchCollapsed: true });
		expect(out.formFolded).toBe(true);
		expect(out.promptWidth).toBe(fill(1480, 12));
	});

	it('grows prompts past their folded-form width when the workbench folds', () => {
		const formFolded = resolveThreePaneLayout(base);
		const both = resolveThreePaneLayout({ ...base, workbenchCollapsed: true });
		expect(both.promptWidth).toBeGreaterThan(formFolded.promptWidth);
	});

	it('fills everything when both panes are folded by the user', () => {
		const out = resolveThreePaneLayout({
			...base,
			panelsWidth: 1864,
			viewportWidth: 1920,
			leftPanelCollapsed: true,
			workbenchCollapsed: true
		});
		expect(out.promptWidth).toBe(fill(1864, 12));
	});

	it('ignores a stored folded width while the workbench is folded', () => {
		const out = resolveThreePaneLayout({
			...base,
			promptPanelWidthFolded: 700,
			workbenchCollapsed: true
		});
		expect(out.promptWidth).toBe(fill(1480, 12));
	});
});
