import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { parsePreviewClip } from '../previewClip';
import { resolveOwnership } from './ownership';

generationMessageRegistry.register('workbench_preview_clip', {
	type: 'workbench_preview_clip',
	handle(message: any, ctx) {
		const { isOwner, adopted } = resolveOwnership(ctx.tab, ctx.generationId);
		if (!isOwner) return;

		const targetTab = adopted
			? {
					...ctx.tab,
					activeGenerationId: adopted.activeGenerationId,
					generation: { ...ctx.tab.generation, ...adopted.generation }
				}
			: ctx.tab;
		const activeIdPatch = adopted ? { activeGenerationId: adopted.activeGenerationId } : {};
		const suppressed = message.preview_suppressed === true;
		const clip = suppressed ? null : parsePreviewClip(message);
		if (!suppressed && !clip) return;

		ctx.tabsStore.updateTab(ctx.tabId, {
			...activeIdPatch,
			generation: {
				...targetTab.generation,
				currentGeneration: {
					...targetTab.generation.currentGeneration,
					preview_clip: clip,
					status: targetTab.generation.currentGeneration?.status || 'running',
					preview_suppressed: suppressed,
					content_flagged: message.content_flagged === true,
					nsfw: message.nsfw === true
				}
			}
		});
	}
});
