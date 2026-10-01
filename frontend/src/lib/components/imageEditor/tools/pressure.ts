import type { ToolPointer } from '../types';

export function pressureScale(pointer: ToolPointer): number {
	if (pointer.pointerType !== 'pen') return 1;
	return Math.min(1, Math.max(0.15, pointer.pressure * 1.4));
}
