import { test } from '@playwright/test';
import { waitForFormPublished } from '../e2e/helpers';
import { EXPANDED_FIELDS, installExpandedForm } from './fixtures';
import { discoverPreset, runScenario, seedGenerationTabs, type PresetHandle } from './perfKit';

const MOVES = 240;
const PERIOD = 80;
const SLIDER = 'section[aria-label] input[type="range"]';

test('slider-drag', async ({ browser }) => {
	const stats = { fields: 0 };
	await runScenario<PresetHandle>(browser, {
		name: 'slider-drag',
		setup: discoverPreset,
		configure: async (context, handle) => {
			await seedGenerationTabs(context, handle, [{ id: 'perf-slider-1', name: 'Perf', prompt: 'a lantern in a harbor' }]);
			await installExpandedForm(context, stats);
		},
		prepare: async (kit) => {
			await kit.page.goto('/generate');
			await waitForFormPublished(kit.page);
			await kit.page.waitForSelector(SLIDER, { state: 'visible', timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await kit.page.waitForTimeout(500);
		},
		measure: async (kit) => {
			if (stats.fields < EXPANDED_FIELDS) throw new Error(`the form was expanded to ${stats.fields} fields, expected ${EXPANDED_FIELDS}`);
			const sliders = await kit.page.locator(SLIDER).count();
			if (sliders < 1) throw new Error('no slider rendered in the form');
			const usable = await kit.page.locator(SLIDER).evaluateAll((nodes) =>
				nodes.findIndex((node) => {
					const input = node as HTMLInputElement;
					const rect = input.getBoundingClientRect();
					return !input.disabled && rect.width > 80 && rect.height > 0 && Number(input.max) > Number(input.min);
				})
			);
			if (usable < 0) throw new Error(`none of the ${sliders} range inputs is an enabled slider with a range`);
			const slider = kit.page.locator(SLIDER).nth(usable);
			await slider.scrollIntoViewIfNeeded();
			const box = (await slider.boundingBox())!;
			const y = box.y + box.height / 2;
			const left = box.x + 4;
			const span = box.width - 8;
			await kit.page.mouse.move(left + span / 2, y);
			const valueBefore = await slider.inputValue();
			const { metrics } = await kit.window(async () => {
				await kit.page.mouse.down();
				for (let i = 0; i < MOVES; i++) {
					const phase = (i % PERIOD) / PERIOD;
					const fraction = phase < 0.5 ? phase * 2 : 2 - phase * 2;
					await kit.page.mouse.move(left + span * fraction, y);
				}
				await kit.page.mouse.move(left + span * 0.75, y);
				await kit.page.mouse.up();
			});
			const valueAfter = await slider.inputValue();
			if (valueAfter === valueBefore) {
				const detail = await slider.evaluate((node, point) => {
					const input = node as HTMLInputElement;
					const top = document.elementFromPoint(point.x, point.y);
					return JSON.stringify({ min: input.min, max: input.max, step: input.step, value: input.value, top: top ? `${top.tagName}.${top.className}` : null, self: top === input });
				}, { x: left + span / 2, y });
				throw new Error(`the slider stayed at ${valueBefore}; the drag did not land ${detail}`);
			}
			return { ...metrics, form_fields: stats.fields, sliders };
		}
	});
});
