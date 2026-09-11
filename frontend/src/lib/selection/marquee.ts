/**
 * DOM wiring for grid multi-select, shared by the History and Library grids.
 * Pure math lives in `./selection`; this module only turns pointer/keyboard
 * events into calls against it.
 */
import {
	applyMarquee,
	idsInMarquee,
	rangeSelection,
	rectFromPoints,
	toggleSelection,
	type SelectionRect
} from './selection';

const MARQUEE_THRESHOLD_PX = 4;

export interface MarqueeVisualState {
	active: boolean;
	rect: SelectionRect | null;
}

export interface MarqueeSelectionOptions {
	/** Row-major id order across the loaded/grouped page, for range math. */
	orderedIds: string[];
	/** The selection as it stands right now - snapshotted at drag start. */
	selectedIds: string[];
	/** CSS attribute selector matching one card's wrapper, e.g. `[data-history-card]`. */
	cardSelector: string;
	getCardId: (el: HTMLElement) => string | undefined;
	setSelection: (ids: string[]) => void;
	onSelectAll: () => void;
	/** Called once a drag actually starts (past the threshold) - a rectangular
	 *  selection makes any single-card anchor ambiguous. */
	onDragStart?: () => void;
	onChange: (state: MarqueeVisualState) => void;
}

/**
 * Rubber-band drag selection over a grid's background, plus window-level Esc
 * (cancel the drag) and Ctrl/Cmd+A (select all, gated to pointer-over-grid or
 * focus-within-grid so it doesn't steal the browser's own select-all
 * elsewhere on the page).
 */
export function marqueeSelection(node: HTMLElement, options: MarqueeSelectionOptions) {
	let opts = options;
	let active = false;
	let rect: SelectionRect | null = null;
	let mode: 'replace' | 'add' = 'replace';
	let preDragSelection: string[] = [];
	let cardRects = new Map<string, SelectionRect>();
	let pointerDownAt: { x: number; y: number; additive: boolean } | null = null;
	let pointerOverGrid = false;

	function notify() {
		opts.onChange({ active, rect });
	}

	function measureCardRects(): Map<string, SelectionRect> {
		const rects = new Map<string, SelectionRect>();
		node.querySelectorAll<HTMLElement>(opts.cardSelector).forEach((el) => {
			const id = opts.getCardId(el);
			if (!id) return;
			const r = el.getBoundingClientRect();
			rects.set(id, { left: r.left, top: r.top, right: r.right, bottom: r.bottom });
		});
		return rects;
	}

	function cancelDrag() {
		active = false;
		rect = null;
		pointerDownAt = null;
		notify();
	}

	function handlePointerDown(event: PointerEvent) {
		if (event.button !== 0) return; // left button only
		const target = event.target as HTMLElement;
		// A pointerdown on a card (plain, Shift or Ctrl) belongs to the card's
		// own click/checkbox handler; only a background press begins a marquee.
		if (target.closest(opts.cardSelector)) return;
		pointerDownAt = {
			x: event.clientX,
			y: event.clientY,
			additive: event.shiftKey || event.ctrlKey || event.metaKey
		};
		node.setPointerCapture(event.pointerId);
	}

	function handlePointerMove(event: PointerEvent) {
		if (!pointerDownAt) return;
		if (!active) {
			const dx = event.clientX - pointerDownAt.x;
			const dy = event.clientY - pointerDownAt.y;
			if (Math.hypot(dx, dy) < MARQUEE_THRESHOLD_PX) return;
			event.preventDefault();
			active = true;
			mode = pointerDownAt.additive ? 'add' : 'replace';
			preDragSelection = opts.selectedIds;
			cardRects = measureCardRects();
			opts.onDragStart?.();
		}
		rect = rectFromPoints(pointerDownAt.x, pointerDownAt.y, event.clientX, event.clientY);
		const intersecting = idsInMarquee(opts.orderedIds, cardRects, rect);
		opts.setSelection(applyMarquee(preDragSelection, intersecting, mode));
		notify();
	}

	function handlePointerUp() {
		cancelDrag();
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && active) {
			opts.setSelection(preDragSelection);
			cancelDrag();
			return;
		}
		if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
			const withinGrid = pointerOverGrid || node.contains(document.activeElement);
			if (!withinGrid) return;
			event.preventDefault();
			opts.onSelectAll();
		}
	}

	// Rects can drift out from under the marquee mid-drag if the page scrolls
	// (viewport-relative coordinates), so re-measure while a drag is live.
	function handleWindowScroll() {
		if (!active) return;
		cardRects = measureCardRects();
	}

	function handlePointerEnter() {
		pointerOverGrid = true;
	}

	function handlePointerLeave() {
		pointerOverGrid = false;
	}

	node.addEventListener('pointerdown', handlePointerDown);
	node.addEventListener('pointermove', handlePointerMove);
	node.addEventListener('pointerup', handlePointerUp);
	node.addEventListener('pointercancel', handlePointerUp);
	node.addEventListener('pointerenter', handlePointerEnter);
	node.addEventListener('pointerleave', handlePointerLeave);
	window.addEventListener('keydown', handleWindowKeydown);
	window.addEventListener('scroll', handleWindowScroll);

	return {
		update(next: MarqueeSelectionOptions) {
			opts = next;
		},
		destroy() {
			node.removeEventListener('pointerdown', handlePointerDown);
			node.removeEventListener('pointermove', handlePointerMove);
			node.removeEventListener('pointerup', handlePointerUp);
			node.removeEventListener('pointercancel', handlePointerUp);
			node.removeEventListener('pointerenter', handlePointerEnter);
			node.removeEventListener('pointerleave', handlePointerLeave);
			window.removeEventListener('keydown', handleWindowKeydown);
			window.removeEventListener('scroll', handleWindowScroll);
		}
	};
}

export type CardSelectKind = 'plain' | 'shift' | 'ctrl';

/** Classifies a card click/activation event into the selection gesture it represents. */
export function classifyCardSelectEvent(event?: MouseEvent | KeyboardEvent): CardSelectKind {
	if (event?.shiftKey) return 'shift';
	if (event?.ctrlKey || event?.metaKey) return 'ctrl';
	return 'plain';
}

/**
 * The next selection (and anchor) for a single card activation. Shift ranges
 * from the current anchor and leaves it unchanged; ctrl and plain both toggle
 * the clicked id and move the anchor onto it - matching desktop file-manager
 * convention (a modifier-held click still starts/extends a selection even
 * outside an explicit "selection mode").
 */
export function resolveCardSelect(
	kind: CardSelectKind,
	orderedIds: string[],
	anchorId: string | null,
	targetId: string,
	current: string[]
): { selection: string[]; nextAnchorId: string | null } {
	if (kind === 'shift') {
		return { selection: rangeSelection(orderedIds, anchorId, targetId, current), nextAnchorId: anchorId };
	}
	return { selection: toggleSelection(current, targetId), nextAnchorId: targetId };
}
