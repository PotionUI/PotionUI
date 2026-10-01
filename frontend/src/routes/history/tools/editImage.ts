import type { MediaEditorRequest } from '$lib/media/editors/types';
import type { MediaToolAvailability, MediaToolContext } from '$lib/tools/tools';

export function editImageAvailability(ctx: MediaToolContext): MediaToolAvailability {
	if (ctx.items.length === 0) return { enabled: false, reason: 'Select 1 image' };
	if (ctx.items.length > 1) return { enabled: false, reason: 'Select exactly 1 image' };
	if (ctx.items[0].kind !== 'image') return { enabled: false, reason: 'Only images can be edited' };
	return { enabled: true };
}

export function editImageRequest(ctx: MediaToolContext): MediaEditorRequest | null {
	if (!editImageAvailability(ctx).enabled) return null;
	const item = ctx.items[0];
	return {
		kind: 'paint',
		source: {
			url: item.url,
			kind: 'image',
			fileName: item.filename,
			width: item.width ?? null,
			height: item.height ?? null
		},
		itemIndex: null
	};
}
