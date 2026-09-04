import { describe, it, expect } from 'vitest';
import { widenPromptPanelForDirector, restorePromptPanelFromDirector } from './generationLayout';

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
