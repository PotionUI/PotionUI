export interface DropHandlers {
	over(event: DragEvent): void;
	drop(event: DragEvent): void;
}

export function dropRow(node: HTMLElement, handlers: DropHandlers) {
	let current = handlers;
	const over = (event: DragEvent) => current.over(event);
	const drop = (event: DragEvent) => current.drop(event);
	node.addEventListener('dragover', over);
	node.addEventListener('drop', drop);
	return {
		update(next: DropHandlers) {
			current = next;
		},
		destroy() {
			node.removeEventListener('dragover', over);
			node.removeEventListener('drop', drop);
		}
	};
}

export function slotFor(from: number, over: number, after: boolean): number {
	if (after) return over > from ? over - 1 : over;
	return over > from ? over : over + 1;
}
