import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { directorShotIdsFor, parseDirectorShotUpdate, withDirectorShotUpdate } from './directorRuns';

generationMessageRegistry.register('director_shot_update', {
	type: 'director_shot_update',
	handle(message: any, ctx) {
		const update = parseDirectorShotUpdate(message);
		if (!update) return;
		const shotIds = directorShotIdsFor(ctx.tab, ctx.generationId);
		if (!shotIds || !shotIds.includes(update.shotId)) return;
		ctx.tabsStore.updateTab(ctx.tabId, {
			directorRuns: withDirectorShotUpdate(ctx.tab, update, Date.now(), ctx.generationId)
		});
	}
});
