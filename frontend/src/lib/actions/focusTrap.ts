const FOCUSABLE_SELECTORS = [
	'a[href]',
	'button:not([disabled])',
	'input:not([disabled])',
	'select:not([disabled])',
	'textarea:not([disabled])',
	'[tabindex]:not([tabindex="-1"])'
].join(', ');

function getFocusableElements(container: HTMLElement): HTMLElement[] {
	return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS)).filter(
		(el) => !el.closest('[hidden]') && getComputedStyle(el).display !== 'none'
	);
}

export default function focusTrap(node: HTMLElement) {
	const previouslyFocused = document.activeElement as HTMLElement | null;

	requestAnimationFrame(() => {
		const focusable = getFocusableElements(node);
		if (focusable.length === 0) return;
		const requested = node.querySelector<HTMLElement>('[data-autofocus]');
		(requested && focusable.includes(requested) ? requested : focusable[0]).focus();
	});

	function handleKeydown(event: KeyboardEvent) {
		if (event.key !== 'Tab') return;

		const focusable = getFocusableElements(node);
		if (focusable.length === 0) return;

		const first = focusable[0];
		const last = focusable[focusable.length - 1];

		if (event.shiftKey) {
			// Shift+Tab: wrap backwards
			if (document.activeElement === first) {
				event.preventDefault();
				last.focus();
			}
		} else {
			// Tab: wrap forwards
			if (document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		}
	}

	node.addEventListener('keydown', handleKeydown);

	return {
		destroy() {
			node.removeEventListener('keydown', handleKeydown);
			// Restore focus to the element that was focused before the modal
			// opened — but only if focus is still where the trap left it (inside
			// the trap, or on <body> if nothing inside ever took it). If the user
			// has already moved on — clicked into a different field the instant
			// the modal's own exit transition started, say — that's a real,
			// deliberate focus change elsewhere in the app; restoring over it
			// would silently steal the keystroke the user is mid-typing into
			// whatever they clicked (this exact race let a modal-close hand a
			// stray "A" to the global "open quick actions" shortcut instead of
			// the field the user had already moved into).
			const current = document.activeElement;
			const stillOurs = current === document.body || (current instanceof Node && node.contains(current));
			if (stillOurs && previouslyFocused && typeof previouslyFocused.focus === 'function') {
				previouslyFocused.focus();
			}
		}
	};
}
