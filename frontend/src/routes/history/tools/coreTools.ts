import type { ComponentType } from 'svelte';
import { registerHistoryTool } from '$lib/history/tools';
import HistoryCompareModal from '../components/HistoryCompareModal.svelte';
import HistoryExportZipModal from '../components/HistoryExportZipModal.svelte';
import HistoryStitchModal from '../components/HistoryStitchModal.svelte';

let registered = false;

/** Puts core's own tools on the shared registry. Safe to call on every mount. */
export function registerCoreHistoryTools(): void {
	if (registered) return;
	registered = true;

	registerHistoryTool({
		id: 'compare',
		label: 'Compare',
		description: 'Diff the parameters and media of two generations',
		icon: 'layers',
		category: 'analyze',
		source: 'core',
		applies: (ctx) =>
			ctx.generations.length === 2
				? { enabled: true }
				: { enabled: false, reason: 'Select exactly 2 generations' },
		component: HistoryCompareModal as unknown as ComponentType
	});

	registerHistoryTool({
		id: 'export-zip',
		label: 'Download .zip',
		description: 'Package the selected generations into one archive',
		icon: 'download',
		category: 'export',
		source: 'core',
		applies: () => ({ enabled: true }),
		component: HistoryExportZipModal as unknown as ComponentType
	});

	registerHistoryTool({
		id: 'stitch',
		label: 'Stitch',
		description: 'Combine the selected images into one',
		icon: 'grid',
		category: 'compose',
		source: 'core',
		applies: (ctx) =>
			ctx.files.filter((entry) => entry.kind === 'image').length >= 2
				? { enabled: true }
				: { enabled: false, reason: 'Select at least 2 images' },
		component: HistoryStitchModal as unknown as ComponentType
	});
}
