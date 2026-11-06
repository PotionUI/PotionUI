import type { RecentLook } from '$lib/filters/recentLooks';
import { mediaToolRegistrations } from '$lib/tools/pluginTools';
import { registerMediaTool, unregisterMediaTools } from '$lib/tools/tools';
import { applyLookAvailability } from '$lib/tools/editMedia';

export function recentLookToolId(lookId: string): string {
	return `look:${lookId}`;
}

export function syncRecentLookTools(looks: readonly RecentLook[]): void {
	unregisterMediaTools((tool) => !!tool.lookId);
	for (const look of looks) {
		registerMediaTool({
			id: recentLookToolId(look.id),
			label: look.name,
			description: `Preview ${look.name} in the Image editor`,
			icon: 'palette',
			category: 'effects',
			source: 'core',
			scopes: ['history', 'library'],
			selection: { min: 1 },
			kinds: ['image'],
			lookId: look.id,
			note: look.group || undefined,
			applies: applyLookAvailability
		});
	}
	mediaToolRegistrations.update((tick) => tick + 1);
}
