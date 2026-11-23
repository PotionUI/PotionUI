import { test } from '@playwright/test';
import { waitForFormPublished } from '../e2e/helpers';
import { installExpandedForm, typedPrompt } from './fixtures';
import { discoverPreset, median, percentile, runScenario, seedGenerationTabs, type PresetHandle } from './perfKit';

const TABS = 8;
const ROUNDS = 3;

const tabToken = (index: number) => `PERFTAB${index}`;

function seededTabs() {
	return Array.from({ length: TABS }, (_, index) => ({
		id: `perf-tab-${index}`,
		name: `Perf ${index + 1}`,
		prompt: `${tabToken(index)} ${typedPrompt(160)}`
	}));
}

test('tab-switch', async ({ browser }) => {
	const stats = { fields: 0 };
	await runScenario<PresetHandle>(browser, {
		name: 'tab-switch',
		setup: discoverPreset,
		configure: async (context, handle) => {
			await seedGenerationTabs(context, handle, seededTabs(), 0);
			await installExpandedForm(context, stats);
		},
		prepare: async (kit) => {
			await kit.page.goto('/generate');
			await waitForFormPublished(kit.page);
			await kit.page.waitForSelector('button.book-tab', { timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(500);
		},
		measure: async (kit) => {
			const times: number[] = [];
			const { metrics } = await kit.window(async () => {
				for (let round = 0; round < ROUNDS; round++) {
					for (let step = 1; step <= TABS; step++) {
						const index = step % TABS;
						const elapsed = await kit.page.evaluate(([i, token]) => (window as any).__perf.switchTab(i, token, 8000), [index, tabToken(index)] as const);
						if (elapsed < 0) throw new Error(`tab ${index} never showed ${tabToken(index)} in the prompt editor`);
						times.push(elapsed);
					}
				}
			});
			return { ...metrics, switch_p50_ms: median(times), switch_p95_ms: percentile(times, 0.95), switches: times.length, form_fields: stats.fields };
		}
	});
});
