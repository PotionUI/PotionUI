import { isTypingInField } from '$lib/utils/editableTarget';
import type { PaintTool } from './types';

export function paintToolForKey(event: KeyboardEvent, tools: readonly Pick<PaintTool, 'id' | 'key'>[]): string | null {
	if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return null;
	if (event.key.length !== 1 || isTypingInField(event)) return null;
	const key = event.key.toLowerCase();
	return tools.find((tool) => tool.key?.toLowerCase() === key)?.id ?? null;
}
