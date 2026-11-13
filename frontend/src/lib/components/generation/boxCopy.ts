export function copyFromBox(event: MouseEvent, box: HTMLElement, button: HTMLButtonElement | null | undefined): void {
	const target = event.target as HTMLElement | null;
	if (target?.closest('button, a')) return;
	const selection = typeof window !== 'undefined' ? window.getSelection() : null;
	if (selection && !selection.isCollapsed && selection.toString() !== '' && box.contains(selection.anchorNode)) return;
	button?.click();
}
