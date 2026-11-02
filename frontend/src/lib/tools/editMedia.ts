import { EDIT_ACTION, planShortcut } from '$lib/tool-tabs/shortcuts';
import type { MediaToolAvailability, MediaToolContext } from '$lib/tools/tools';

export function editMediaAvailability(ctx: MediaToolContext): MediaToolAvailability {
	if (ctx.items.length === 0) return { enabled: false, reason: 'Select an image or a video' };
	if (!planShortcut(EDIT_ACTION, ctx.items)) return { enabled: false, reason: 'Only images and videos can be edited' };
	return { enabled: true };
}

export function editMediaLabel(ctx: MediaToolContext): string {
	const plan = planShortcut(EDIT_ACTION, ctx.items);
	if (!plan) return 'Open in editor';
	return plan.length > 1 ? 'Open in editor' : plan[0].tool === 'video' ? 'Open in Video editor' : 'Open in Image editor';
}

export function applyLookAvailability(ctx: MediaToolContext): MediaToolAvailability {
	if (ctx.items.length === 0 || ctx.items.some((item) => item.kind !== 'image')) {
		return { enabled: false, hidden: true };
	}
	return { enabled: true };
}
