import { test } from '@playwright/test';
import { discoverPreset, networkMetrics, runScenario, seedGenerationTabs, type PresetHandle } from './perfKit';

test('cold-generate', async ({ browser }) => {
	await runScenario<PresetHandle>(browser, {
		name: 'cold-generate',
		probe: { autoOpen: true, interactive: true },
		trackNetwork: true,
		setup: discoverPreset,
		configure: (context, handle) => seedGenerationTabs(context, handle, [{ id: 'perf-cold-1', name: 'Perf', prompt: '' }]),
		measure: async (kit) => {
			let apiRequests = 0;
			kit.page.on('request', (request) => {
				if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests += 1;
			});
			await kit.page.goto('/generate');
			await kit.page.waitForFunction(() => (window as any).__perf.state.interactive !== null, null, { timeout: 60000, polling: 50 });
			const { metrics } = await kit.end({});
			await kit.page.waitForTimeout(750);
			const observed = await kit.observed();
			const network = kit.network();
			kit.extra = {
				compression: network.compression,
				js_files: network.jsFiles.map((file) => ({ path: new URL(file.url).pathname, bytes: file.bytes })),
				by_type: network.byType
			};
			return {
				...metrics,
				fcp_ms: observed.fcp ?? 0,
				lcp_ms: observed.lcp ?? 0,
				t_interactive_ms: observed.interactive ?? 0,
				...networkMetrics(network, apiRequests)
			};
		}
	});
});
