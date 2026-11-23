import { test } from '@playwright/test';
import { waitForFormPublished } from '../e2e/helpers';
import { installExpandedForm, typedPrompt } from './fixtures';
import { discoverPreset, persistMetrics, runScenario, seedGenerationTabs, typingMetrics, type PresetHandle } from './perfKit';

const TABS = 8;
const EDITOR = '.inline-chip-editor[role="textbox"]';

test('long-prompt-typing', async ({ browser }) => {
	const stats = { fields: 0 };
	const text = typedPrompt(800);
	await runScenario<PresetHandle>(browser, {
		name: 'long-prompt-typing',
		setup: discoverPreset,
		configure: async (context, handle) => {
			const tabs = Array.from({ length: TABS }, (_, index) => ({ id: `perf-typing-${index}`, name: `Perf ${index + 1}`, prompt: index === 0 ? '' : typedPrompt(160) }));
			await seedGenerationTabs(context, handle, tabs, 0);
			await installExpandedForm(context, stats);
		},
		prepare: async (kit) => {
			await kit.page.goto('/generate');
			await waitForFormPublished(kit.page);
			await kit.page.locator(EDITOR).first().waitFor({ state: 'visible', timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(500);
		},
		measure: async (kit) => {
			const editor = kit.page.locator(EDITOR).first();
			await editor.click();
			const { raw, metrics } = await kit.window(async () => {
				await kit.page.keyboard.type(text, { delay: 0 });
				await kit.page.waitForTimeout(1500);
			});
			const typing = typingMetrics(raw);
			const persist = persistMetrics(raw, 'potionui_tabs_state');
			const editorChars = (await editor.innerText()).length;
			if (editorChars < text.length * 0.9 || editorChars > text.length * 1.25) {
				throw new Error(`editor holds ${editorChars} chars after typing ${text.length}; the typing did not land`);
			}
			if (typing.event_count < text.length / 2) throw new Error(`only ${typing.event_count} keydown/input interactions recorded for ${text.length} typed characters`);
			if (persist.persist_writes < 1) throw new Error('typing never wrote the tabs state to localStorage');
			return { ...metrics, ...typing, ...persist, typed_chars: text.length, editor_chars: editorChars, form_fields: stats.fields };
		}
	});
});
