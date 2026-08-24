// Pane body keyboard nav is DOM-side: consumers write their own markup for
// rows (no item array to index), so Up/Down/Home/End resolve against the
// currently rendered [data-pane-row] elements rather than a data model.

export type NavKey = 'ArrowUp' | 'ArrowDown' | 'Home' | 'End';

// `current` is the focused row's index, or -1 when nothing in the pane has
// focus yet. Returns -1 only when there is nothing to focus (count === 0).
export function nextIndex(count: number, current: number, key: NavKey): number {
	if (count <= 0) return -1;
	switch (key) {
		case 'Home':
			return 0;
		case 'End':
			return count - 1;
		case 'ArrowDown':
			return current < 0 ? 0 : Math.min(current + 1, count - 1);
		case 'ArrowUp':
			return current < 0 ? count - 1 : Math.max(current - 1, 0);
	}
}

export function focusableRows(container: HTMLElement): HTMLElement[] {
	return Array.from(container.querySelectorAll<HTMLElement>('[data-pane-row]:not([data-disabled])'));
}
