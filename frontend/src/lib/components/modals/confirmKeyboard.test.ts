import { describe, expect, it, vi } from 'vitest';
import {
	createConfirmSettlementGate,
	getConfirmKeyboardAction,
	resolveConfirmKeydown,
	settleIfEligible
} from './confirmKeyboard';

function event(
	key: string,
	target: { tagName?: string; isContentEditable?: boolean; parentElement?: object | null } | null = null,
	repeat = false
) {
	return { key, target, repeat } as KeyboardEvent;
}

describe('confirm keyboard interaction', () => {
	it('maps Enter to accept and Escape to reject', () => {
		expect(getConfirmKeyboardAction(event('Enter'))).toEqual({ action: 'confirm', suppress: true });
		expect(getConfirmKeyboardAction(event('Escape'))).toEqual({ action: 'cancel', suppress: true });
	});

	it('leaves Enter in editable descendants untouched', () => {
		expect(getConfirmKeyboardAction(event('Enter', { tagName: 'INPUT' }))).toEqual({
			action: null,
			suppress: false
		});
		expect(getConfirmKeyboardAction(event('Enter', { isContentEditable: true }))).toEqual({
			action: null,
			suppress: false
		});
	});

	it('suppresses repeated Enter and Escape without settling again', () => {
		expect(getConfirmKeyboardAction(event('Enter', null, true))).toEqual({
			action: null,
			suppress: true
		});
		expect(getConfirmKeyboardAction(event('Escape', null, true))).toEqual({
			action: null,
			suppress: true
		});
	});

	it('settles once until the next dialog open resets the gate', () => {
		const gate = createConfirmSettlementGate();
		const first = vi.fn();
		const second = vi.fn();

		expect(gate.settle(first)).toBe(true);
		expect(gate.settle(second)).toBe(false);
		expect(first).toHaveBeenCalledTimes(1);
		expect(second).not.toHaveBeenCalled();

		gate.reset();
		expect(gate.settle(second)).toBe(true);
		expect(second).toHaveBeenCalledTimes(1);
	});

	it('does not let a held key settle the next keyed dialog', () => {
		const firstGate = createConfirmSettlementGate();
		const first = vi.fn();
		const second = vi.fn();
		const initialEnter = getConfirmKeyboardAction(event('Enter'));
		expect(initialEnter.action).toBe('confirm');
		firstGate.settle(first);

		// A queued request remounts a fresh ConfirmModal, but the browser may
		// still emit repeat keydowns from the key that settled the first one.
		const secondGate = createConfirmSettlementGate();
		const heldEnter = getConfirmKeyboardAction(event('Enter', null, true));
		expect(heldEnter.action).toBeNull();
		if (heldEnter.action) secondGate.settle(second);
		expect(second).not.toHaveBeenCalled();

		const nextEnter = getConfirmKeyboardAction(event('Enter'));
		expect(nextEnter.action).toBe('confirm');
		if (nextEnter.action) secondGate.settle(second);
		expect(first).toHaveBeenCalledTimes(1);
		expect(second).toHaveBeenCalledTimes(1);
	});
});

// resolveConfirmKeydown is the exact function ConfirmModal.svelte's
// <svelte:window on:keydown> handler calls; these exercise the composed
// resolve-through-the-gate behavior, not getConfirmKeyboardAction again.
describe('resolveConfirmKeydown (ConfirmModal wiring)', () => {
	it('confirms on Enter and cancels on Escape through the helper', () => {
		const confirm = vi.fn();
		const cancel = vi.fn();
		const handlers = { confirm, cancel };

		const confirmGate = createConfirmSettlementGate();
		expect(resolveConfirmKeydown(event('Enter'), true, confirmGate, handlers)).toBe(true);
		expect(confirm).toHaveBeenCalledTimes(1);
		expect(cancel).not.toHaveBeenCalled();

		const cancelGate = createConfirmSettlementGate();
		expect(resolveConfirmKeydown(event('Escape'), true, cancelGate, handlers)).toBe(true);
		expect(cancel).toHaveBeenCalledTimes(1);
		expect(confirm).toHaveBeenCalledTimes(1);
	});

	it('is a full no-op while inactive (dialog closed or busy)', () => {
		const confirm = vi.fn();
		const cancel = vi.fn();
		const gate = createConfirmSettlementGate();

		expect(resolveConfirmKeydown(event('Enter'), false, gate, { confirm, cancel })).toBe(false);
		expect(resolveConfirmKeydown(event('Escape'), false, gate, { confirm, cancel })).toBe(false);
		expect(confirm).not.toHaveBeenCalled();
		expect(cancel).not.toHaveBeenCalled();

		// The gate was never touched, so a later active call still resolves.
		expect(resolveConfirmKeydown(event('Enter'), true, gate, { confirm, cancel })).toBe(true);
		expect(confirm).toHaveBeenCalledTimes(1);
	});

	it('the settlement gate stops a stray keydown from resolving twice', () => {
		const confirm = vi.fn();
		const cancel = vi.fn();
		const gate = createConfirmSettlementGate();
		const handlers = { confirm, cancel };

		// Confirm click (or first Enter) settles the dialog...
		resolveConfirmKeydown(event('Enter'), true, gate, handlers);
		// ...a queued Escape landing right after (dialog already resolving)
		// must not also fire cancel.
		resolveConfirmKeydown(event('Escape'), true, gate, handlers);

		expect(confirm).toHaveBeenCalledTimes(1);
		expect(cancel).not.toHaveBeenCalled();
	});

	it('leaves Enter in editable descendants untouched, same as the raw helper', () => {
		const confirm = vi.fn();
		const cancel = vi.fn();
		const gate = createConfirmSettlementGate();

		const suppress = resolveConfirmKeydown(event('Enter', { tagName: 'INPUT' }), true, gate, {
			confirm,
			cancel
		});

		expect(suppress).toBe(false);
		expect(confirm).not.toHaveBeenCalled();
	});
});

// settleIfEligible is what HistoryAddTagModal.svelte's submit()/dismiss()
// wrap around the shared gate, since "confirm" there carries an extra
// precondition (a non-empty tag name) beyond the dialog being open.
describe('settleIfEligible (history modal wiring)', () => {
	it('does not burn the gate on an ineligible attempt, so a later eligible one still settles', () => {
		const gate = createConfirmSettlementGate();
		const createTag = vi.fn();

		expect(settleIfEligible(gate, false, createTag)).toBe(false);
		expect(createTag).not.toHaveBeenCalled();

		expect(settleIfEligible(gate, true, createTag)).toBe(true);
		expect(createTag).toHaveBeenCalledTimes(1);
	});

	it('a converted history modal responds to both Enter (submit) and Escape (cancel)', () => {
		const gate = createConfirmSettlementGate();
		const createTag = vi.fn();
		const close = vi.fn();
		let tagName = 'my-tag';

		function submit() {
			const { action } = getConfirmKeyboardAction(event('Enter'));
			if (action === 'confirm') settleIfEligible(gate, !!tagName.trim(), createTag);
		}
		function dismiss() {
			const { action } = getConfirmKeyboardAction(event('Escape'));
			if (action === 'cancel') settleIfEligible(gate, true, close);
		}

		submit();
		expect(createTag).toHaveBeenCalledTimes(1);

		// The gate is already settled by the Enter above, so a stray Escape
		// right after cannot also cancel the same dialog instance.
		dismiss();
		expect(close).not.toHaveBeenCalled();
	});

	it('an eligible attempt still only settles once', () => {
		const gate = createConfirmSettlementGate();
		const createTag = vi.fn();

		expect(settleIfEligible(gate, true, createTag)).toBe(true);
		expect(settleIfEligible(gate, true, createTag)).toBe(false);
		expect(createTag).toHaveBeenCalledTimes(1);
	});
});
