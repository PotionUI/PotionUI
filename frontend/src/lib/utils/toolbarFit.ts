export interface ToolbarFitInput {
	available: number;
	gap: number;
	order: string[];
	collapseOrder: string[];
	widths: Record<string, number>;
}

export function fitToolbar({ available, gap, order, collapseOrder, widths }: ToolbarFitInput): string[] {
	const hidden = new Set<string>();
	const used = () => {
		const shown = order.filter((key) => !hidden.has(key));
		return shown.reduce((sum, key) => sum + (widths[key] ?? 0), 0) + gap * shown.length;
	};
	for (const key of collapseOrder) {
		if (used() <= available) break;
		if (order.includes(key)) hidden.add(key);
	}
	return order.filter((key) => hidden.has(key));
}

export interface ToolbarFitParams {
	order: string[];
	collapseOrder: string[];
	onChange: (hidden: string[]) => void;
}

const SLOT = '[data-toolbar-action]';
const MORE = '[data-toolbar-more]';
const MORE_FALLBACK_WIDTH = 28;

export function toolbarFit(node: HTMLElement, initial: ToolbarFitParams) {
	let params = initial;
	let last = '';
	const widths: Record<string, number> = {};
	const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => compute());

	function px(value: string) {
		const n = parseFloat(value);
		return Number.isFinite(n) ? n : 0;
	}

	function compute() {
		if (node.clientWidth === 0) return;
		const style = getComputedStyle(node);
		const gap = px(style.columnGap) || 8;
		let fixed = 0;
		let fixedCount = 0;
		let moreWidth = MORE_FALLBACK_WIDTH;
		for (const child of Array.from(node.children) as HTMLElement[]) {
			if (child.matches(SLOT)) {
				widths[child.dataset.toolbarAction as string] = child.offsetWidth;
				observer?.observe(child);
			} else if (child.matches(MORE)) {
				moreWidth = child.offsetWidth || MORE_FALLBACK_WIDTH;
			} else if (!child.classList.contains('toolbar-spacer')) {
				fixed += child.offsetWidth;
				fixedCount += 1;
			}
		}
		const inner = node.clientWidth - px(style.paddingLeft) - px(style.paddingRight);
		const available = inner - fixed - gap * fixedCount - moreWidth - gap;
		const hidden = fitToolbar({
			available,
			gap,
			order: params.order,
			collapseOrder: params.collapseOrder,
			widths
		});
		const signature = hidden.join('|');
		if (signature !== last) {
			last = signature;
			params.onChange(hidden);
		}
	}

	observer?.observe(node);
	queueMicrotask(compute);

	return {
		update(next: ToolbarFitParams) {
			params = next;
			queueMicrotask(compute);
		},
		destroy() {
			observer?.disconnect();
		}
	};
}
