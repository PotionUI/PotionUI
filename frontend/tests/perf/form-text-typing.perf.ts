import { test } from '@playwright/test';
import { waitForFormPublished } from '../e2e/helpers';
import { EXPANDED_FIELDS, installExpandedForm, typedPrompt } from './fixtures';
import { discoverPreset, runScenario, seedGenerationTabs, typingMetrics, type PresetHandle } from './perfKit';

const CHARS = 300;
const TEXT_INPUT = 'section[aria-label] input[type="text"]:not([readonly]):not([disabled])';

function watchFormMutations() {
	const w = window as any;
	w.__formMutations = 0;
	new MutationObserver((records) => {
		for (const record of records) {
			const target = record.target as Element;
			const element = target.nodeType === 1 ? target : target.parentElement;
			if (element?.closest('section[aria-label]')) w.__formMutations += 1;
		}
	}).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
}

test('form-text-typing', async ({ browser }) => {
	const stats = { fields: 0 };
	const text = typedPrompt(CHARS).replace(/[{}|]/g, '');
	await runScenario<PresetHandle>(browser, {
		name: 'form-text-typing',
		setup: discoverPreset,
		configure: async (context, handle) => {
			await seedGenerationTabs(context, handle, [{ id: 'perf-text-1', name: 'Perf', prompt: 'a lantern in a harbor' }]);
			await installExpandedForm(context, stats);
		},
		prepare: async (kit) => {
			await kit.page.goto('/generate');
			await waitForFormPublished(kit.page);
			await kit.page.waitForSelector(TEXT_INPUT, { state: 'visible', timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(500);
			await kit.page.evaluate(watchFormMutations);
		},
		measure: async (kit) => {
			if (stats.fields < EXPANDED_FIELDS) throw new Error(`the form was expanded to ${stats.fields} fields, expected ${EXPANDED_FIELDS}`);
			const input = kit.page.locator(TEXT_INPUT).first();
			await input.scrollIntoViewIfNeeded();
			await input.click();
			await kit.page.evaluate(() => ((window as any).__formMutations = 0));
			const { raw, metrics } = await kit.window(async () => {
				await kit.page.keyboard.type(text, { delay: 0 });
				await kit.page.waitForTimeout(500);
			});
			const value = await input.inputValue();
			if (value.length < text.length * 0.9) throw new Error(`the text input holds ${value.length} chars after typing ${text.length}`);
			const formMutations = (await kit.page.evaluate(() => (window as any).__formMutations)) as number;
			return { ...metrics, ...typingMetrics(raw), typed_chars: text.length, form_fields: stats.fields, form_mutations_per_char: formMutations / text.length };
		}
	});
});
