import { getContext, setContext } from 'svelte';

export interface FrameContext {
	readonly spaceHeld: boolean;
	readonly sheetHost: HTMLElement | null;
	readonly panelSide: 'left' | 'right';
}

const KEY = Symbol('editor-frame');

export function setFrameContext(context: FrameContext): void {
	setContext(KEY, context);
}

export function getFrameContext(): FrameContext | null {
	return getContext<FrameContext | undefined>(KEY) ?? null;
}
