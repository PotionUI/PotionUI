export type ConfirmKeyboardAction = 'confirm' | 'cancel';

export interface KeyboardEventLike {
	key: string;
	repeat: boolean;
	target: EventTarget | null;
}

interface KeyboardTargetLike {
	tagName?: string;
	isContentEditable?: boolean;
	parentElement?: KeyboardTargetLike | null;
}

export interface ConfirmKeyboardResult {
	action: ConfirmKeyboardAction | null;
	suppress: boolean;
}

/**
 * Returns the confirm-dialog action for a local keydown event. Repeat events
 * remain suppressed so a held key cannot fall through to a native button click.
 */
export function getConfirmKeyboardAction(event: KeyboardEventLike): ConfirmKeyboardResult {
	if (event.key === 'Escape') {
		return { action: event.repeat ? null : 'cancel', suppress: true };
	}

	if (event.key !== 'Enter' || isEditableTarget(event.target)) {
		return { action: null, suppress: false };
	}

	return { action: event.repeat ? null : 'confirm', suppress: true };
}

export type ConfirmSettlementGate = ReturnType<typeof createConfirmSettlementGate>;

export function createConfirmSettlementGate() {
	let settled = false;

	return {
		settle(callback: () => void): boolean {
			if (settled) return false;
			settled = true;
			callback();
			return true;
		},
		reset() {
			settled = false;
		}
	};
}

export interface ConfirmSettlementHandlers {
	confirm(): void;
	cancel(): void;
}

/**
 * The single keydown-to-resolution path shared by every confirm-style
 * dialog: resolves the action for `event`, routes it through `gate` so a
 * repeat or a race with a pointer click can't resolve twice, and reports
 * whether the caller should call `event.preventDefault()`. A `false` active
 * flag (dialog closed, or a mutation already in flight) is a full no-op —
 * the gate is never touched and no default is suppressed.
 */
export function resolveConfirmKeydown(
	event: KeyboardEventLike,
	active: boolean,
	gate: ConfirmSettlementGate,
	handlers: ConfirmSettlementHandlers
): boolean {
	if (!active) return false;
	const { action, suppress } = getConfirmKeyboardAction(event);
	if (action === 'confirm') gate.settle(handlers.confirm);
	else if (action === 'cancel') gate.settle(handlers.cancel);
	return suppress;
}

/**
 * Settles `gate` with `callback` only when `eligible` — for confirm actions
 * that carry their own precondition (e.g. a non-empty input) on top of the
 * dialog being open. An ineligible call leaves the gate untouched so a later,
 * eligible attempt can still settle it.
 */
export function settleIfEligible(
	gate: ConfirmSettlementGate,
	eligible: boolean,
	callback: () => void
): boolean {
	if (!eligible) return false;
	return gate.settle(callback);
}

function isEditableTarget(target: EventTarget | null): boolean {
	let element = target as KeyboardTargetLike | null;
	while (element) {
		const tag = element.tagName?.toUpperCase();
		if (
			tag === 'INPUT' ||
			tag === 'TEXTAREA' ||
			tag === 'SELECT' ||
			element.isContentEditable
		) {
			return true;
		}
		element = element.parentElement ?? null;
	}
	return false;
}
