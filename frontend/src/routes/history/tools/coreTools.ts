import type { ComponentType } from 'svelte';
import { recentLooks } from '$lib/filters/recentLooks';
import { registerMediaTool } from '$lib/tools/tools';
import { registerEntryTools } from '$lib/tools/entryTools';
import HistoryCompareModal from '../components/HistoryCompareModal.svelte';
import HistoryExportZipModal from '../components/HistoryExportZipModal.svelte';
import HistoryStitchModal from '../components/HistoryStitchModal.svelte';
import { applyLookAvailability, editMediaAvailability, editMediaLabel, sliceSheetAvailability } from '$lib/tools/editMedia';
import { SLICE_ACTION } from '$lib/tool-tabs/shortcuts';
import { syncRecentLookTools } from './recentLookTools';

let registered = false;

/** Puts core's own tools on the shared registry. Safe to call on every mount. */
export function registerCoreHistoryTools(): void {
	if (registered) return;
	registered = true;

	registerEntryTools();

	registerMediaTool({
		id: 'compare',
		label: 'Compare',
		description: 'Diff the parameters and media of two generations',
		icon: 'layers',
		category: 'analyze',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 2, max: 2 },
		// History compares two *generations* (their full params, not just their
		// media) - two items from the same batch-of-N generation is still one
		// generation, and can't be diffed. Library has no generation concept,
		// so there `items` IS the selection to count.
		applies: (ctx) => {
			const count = ctx.scope === 'history' ? ctx.generations.length : ctx.items.length;
			const noun = ctx.scope === 'history' ? 'generations' : 'items';
			return count === 2 ? { enabled: true } : { enabled: false, reason: `Select exactly 2 ${noun}` };
		},
		component: HistoryCompareModal as unknown as ComponentType
	});

	registerMediaTool({
		id: 'export-zip',
		label: 'Download .zip',
		description: 'Package the selected media into one archive',
		icon: 'download',
		category: 'export',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 2 },
		applies: () => ({ enabled: true }),
		component: HistoryExportZipModal as unknown as ComponentType
	});

	registerMediaTool({
		id: 'stitch',
		label: 'Stitch',
		description: 'Combine the selected images into one',
		icon: 'grid',
		category: 'compose',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 2 },
		applies: (ctx) =>
			ctx.items.filter((item) => item.kind === 'image').length >= 2
				? { enabled: true }
				: { enabled: false, reason: 'Select at least 2 images' },
		component: HistoryStitchModal as unknown as ComponentType
	});

	registerMediaTool({
		id: 'edit-image',
		label: 'Open in editor',
		description: 'Open the selection in the Image or Video editor',
		icon: 'sliders',
		category: 'compose',
		shortcut: 'E',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 1 },
		kinds: ['image', 'video'],
		labelFor: editMediaLabel,
		applies: editMediaAvailability
	});

	registerMediaTool({
		id: SLICE_ACTION,
		label: 'Slice into references',
		description: 'Cut a sheet into separate images',
		icon: 'editor-slice',
		category: 'edit',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 1 },
		kinds: ['image'],
		applies: sliceSheetAvailability
	});

	registerMediaTool({
		id: 'apply-look',
		label: 'Apply a look…',
		description: 'Pick a look in the Image editor',
		icon: 'palette',
		category: 'effects',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 1 },
		kinds: ['image'],
		applies: applyLookAvailability
	});

	recentLooks.subscribe(syncRecentLookTools);
}
