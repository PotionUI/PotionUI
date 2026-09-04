// @vitest-environment jsdom
//
// destroy() restores focus to whatever was focused before the trap mounted —
// but only when focus is STILL where the trap left it (inside the trap, or on
// <body> if nothing inside ever took it). A trap that unconditionally
// restored let a modal's own exit-transition-delayed cleanup steal focus back
// from wherever the user had already, deliberately, moved on to in the
// meantime (real-world case: a global single-key shortcut fired because the
// restored focus swallowed the keystroke meant for a field the user had
// already clicked into).
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import focusTrap from './focusTrap';

let root: HTMLDivElement;

beforeEach(() => {
	root = document.createElement('div');
	document.body.appendChild(root);
});

afterEach(() => {
	root.remove();
	document.body.innerHTML = '';
});

function trigger(): HTMLButtonElement {
	const btn = document.createElement('button');
	btn.textContent = 'open';
	document.body.appendChild(btn);
	return btn;
}

function trapNodeWith(...children: HTMLElement[]): HTMLDivElement {
	const node = document.createElement('div');
	children.forEach((c) => node.appendChild(c));
	root.appendChild(node);
	return node;
}

describe('focusTrap destroy()', () => {
	it('restores focus to the pre-trap element when focus is still inside the trap', () => {
		const openTrigger = trigger();
		openTrigger.focus();
		expect(document.activeElement).toBe(openTrigger);

		const inTrapButton = document.createElement('button');
		const node = trapNodeWith(inTrapButton);
		const action = focusTrap(node);
		inTrapButton.focus();
		expect(document.activeElement).toBe(inTrapButton);

		action.destroy?.();

		expect(document.activeElement).toBe(openTrigger);
	});

	it('restores focus when nothing inside the trap ever took it (focus still on body)', () => {
		const openTrigger = trigger();
		openTrigger.focus();

		const inTrapButton = document.createElement('button');
		const node = trapNodeWith(inTrapButton);
		const action = focusTrap(node);
		// Nothing inside the trap was ever focused (e.g. rAF's initial-focus
		// step hasn't run yet, or the trap has no focusable content) — focus is
		// still wherever it lands by default, <body>.
		(document.activeElement as HTMLElement | null)?.blur?.();
		expect(document.activeElement).toBe(document.body);

		action.destroy?.();

		expect(document.activeElement).toBe(openTrigger);
	});

	it('leaves focus untouched when the user has already moved it outside the trap', () => {
		const openTrigger = trigger();
		openTrigger.focus();

		const inTrapButton = document.createElement('button');
		const node = trapNodeWith(inTrapButton);
		const action = focusTrap(node);
		inTrapButton.focus();

		// The user clicks into some unrelated field while the trap's owner is
		// mid-close (e.g. a modal's exit transition) — a real, deliberate focus
		// change this destroy() must not clobber.
		const elsewhere = document.createElement('input');
		document.body.appendChild(elsewhere);
		elsewhere.focus();
		expect(document.activeElement).toBe(elsewhere);

		action.destroy?.();

		expect(document.activeElement).toBe(elsewhere);
	});
});
