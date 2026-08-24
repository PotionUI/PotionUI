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
			// Restore focus to the element that was focused before the modal opened
			if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
				previouslyFocused.focus();
			}
		}
	};
}
