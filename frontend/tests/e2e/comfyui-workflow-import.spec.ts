import { test, expect, type Page } from '@playwright/test';
import { readdirSync, readFileSync, rmSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Admin -> Plugins -> ComfyUI Backend -> "Import workflow": the 5-step
// wizard (Source -> Form -> History -> Requirements -> Done), paste the
// Export (API) fixture, walk every step, land on the created preset via
// "Open in Presets". The throwaway backend runs with cwd = the real repo
// checkout (see tests/e2e/harness/e2e_harness.py), so emit_preset writes
// into the real, .gitignored content/presets/local/ - the unique family
// name plus the cleanup below keep this journey from leaving anything
// behind.
test.use({ viewport: { width: 1440, height: 900 } });

const JOURNEY = 'comfyui-workflow-import';
const PLUGIN_ID = 'comfyui-backend';
const REPO_ROOT = resolve(__dirname, '../../..');
const FIXTURE_PATH = resolve(
	REPO_ROOT,
	'content/plugins/marketplace/comfyui-backend/tests/fixtures/sdxl_basic_api.json'
);

// Isolates one `- name: <fieldName>` list item out of a tab YAML's `fields:`
// block (sibling fields, and a section's other fields sharing the file,
// would otherwise leak into a substring/toContain check on the whole file).
function extractFieldBlock(yamlText: string, fieldName: string): string {
	const lines = yamlText.split('\n');
	const startIdx = lines.findIndex((l) => new RegExp(`^\\s*- name: ${fieldName}\\s*$`).test(l));
	if (startIdx === -1) return '';
	const indent = lines[startIdx].match(/^\s*/)?.[0].length ?? 0;
	const block = [lines[startIdx]];
	for (let i = startIdx + 1; i < lines.length; i++) {
		const line = lines[i];
		if (line.trim() === '') {
			block.push(line);
			continue;
		}
		const lineIndent = line.match(/^\s*/)?.[0].length ?? 0;
		if (lineIndent <= indent && line.trim().startsWith('- ')) break;
		if (lineIndent < indent) break;
		block.push(line);
	}
	return block.join('\n');
}

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('Admin > Plugins > ComfyUI Backend - import a workflow through the wizard into a lint-clean preset', async ({
	page
}) => {
	// Import + reload + modify (edit -> update) + delete, ~12 screenshots and
	// several wizard round trips - comfortably over the 30s default.
	test.setTimeout(90_000);
	const familyId = `e2e-import-${Date.now()}`;
	const createdPresetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);

	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);

		// New plugins start disabled (src/features/plugins/operations/scan.py),
		// so a fresh throwaway instance never shows this plugin's tabs until
		// this runs.
		await apiPost(page, '/api/plugins/scan', token);
		const pluginsList = await apiGet(page, '/api/plugins', token);
		const pluginRow = (pluginsList.data || []).find((p: any) => p.id === PLUGIN_ID);
		if (!pluginRow) {
			test.skip(true, `'${PLUGIN_ID}' was not discovered on this throwaway instance.`);
			return;
		}
		if (!pluginRow.enabled) {
			await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
		}

		await page.goto('/admin?tab=plugins');
		await page.waitForTimeout(500);

		const pluginListRow = page.getByText('ComfyUI Backend', { exact: false }).first();
		if ((await pluginListRow.count()) === 0) {
			test.skip(
				true,
				"'ComfyUI Backend' is not present in the plugin list - the plugin frontend isn't mounted on this build " +
					'(content/plugins/local/comfyui-backend, if present, ships without a frontend and shadows the marketplace copy).'
			);
			return;
		}
		await pluginListRow.click();
		await screenshot(page, JOURNEY, '01-plugin-selected');

		const tabNav = page.locator('nav[aria-label="Plugin details"]');
		await expect(tabNav).toBeVisible();

		// Settings gets its own card background (DetailSection), same as Overview -
		// captured here for the maintainer's visual check, unrelated to the rest
		// of this journey.
		const settingsTab = tabNav.getByText('Settings', { exact: false });
		if ((await settingsTab.count()) > 0) {
			await settingsTab.click();
			await page.waitForTimeout(300);
			await screenshot(page, JOURNEY, '01b-settings-tab');
		}

		const importTab = tabNav.getByText('Import workflow', { exact: false });
		if ((await importTab.count()) === 0) {
			test.skip(true, "'Import workflow' tab not present - the admin_tabs hook wasn't picked up on this build.");
			return;
		}
		await importTab.click();

		const wizard = page.locator('[data-import-wizard]');
		await expect(wizard).toBeVisible();
		await screenshot(page, JOURNEY, '02-wizard-source');

		// Step 1: Source.
		const workflowJson = readFileSync(FIXTURE_PATH, 'utf-8');
		await wizard.locator('textarea[data-import-json-input]').fill(workflowJson);
		await wizard.locator('button[data-import-analyze]').click();

		// Step 2: Form - the default_form designer (left: workflow inputs,
		// right: tabs/field cards).
		await expect(wizard.locator('[data-import-form-inputs]')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '03-wizard-form');

		// The mapping toggle has its own icon, distinct from the adjacent
		// Move up/down chevrons it used to share.
		const fieldsList = wizard.locator('[data-import-form-items] .di-field-card');
		const firstFieldCard = fieldsList.first();
		await expect(firstFieldCard).toHaveCount(1);
		await expect(firstFieldCard.locator('button[data-action="toggle-mapping"] use[href="#i-link"]')).toHaveCount(1);

		// Cross-tab move: add a second tab, "Move to" a field into it via the
		// popover, then drag another field back onto the original tab header.
		const tabsBar = wizard.locator('[data-import-form-tabs]');
		const originalTab = tabsBar.locator('.di-tab').first();
		const originalTabId = await originalTab.getAttribute('data-tab-id');

		await wizard.locator('[data-action="add-tab"]').click();
		const newTab = tabsBar.locator('.di-tab').last();
		const newTabId = await newTab.getAttribute('data-tab-id');
		expect(newTabId).not.toBe(originalTabId);

		await originalTab.click();
		const fieldCountBefore = await fieldsList.count();
		expect(fieldCountBefore).toBeGreaterThan(0);

		// Tab icon + display mode: the popover's Icon/Display selects, and the
		// icon that shows up next to the tab's own label once one is picked.
		await originalTab.locator('[data-action="tab-menu"]').click();
		const iconPopover = page.locator('.tab-popover');
		await iconPopover.locator('select[data-action="tab-icon"]').selectOption('lora');
		await iconPopover.locator('select[data-action="tab-display"]').selectOption('icon_label');
		await wizard.locator('.di-left-title').click(); // dismiss the popover (outside click)
		await expect(iconPopover).toHaveCount(0);
		await expect(originalTab.locator('use[href="#ti-lora"]')).toHaveCount(1);
		await screenshot(page, JOURNEY, '03a-wizard-tab-icon');

		await fieldsList.first().locator('button[data-action="move-to-tab-menu"]').click();
		await page.locator(`.tab-popover button[data-target-tab="${newTabId}"]`).click();
		// Moving a field switches the view to the tab it landed on.
		await expect(newTab).toHaveClass(/active/);
		await expect(fieldsList).toHaveCount(1);
		await screenshot(page, JOURNEY, '03b-wizard-move-to-tab');

		await originalTab.click();
		await expect(fieldsList).toHaveCount(fieldCountBefore - 1);

		// Drag the moved field (now the new tab's only item) back onto the
		// original tab's header.
		await newTab.click();
		const movedCard = fieldsList.first();
		await movedCard.locator('.drag-handle').dragTo(originalTab);
		await expect(originalTab).toHaveClass(/active/);
		await expect(fieldsList).toHaveCount(fieldCountBefore);
		await screenshot(page, JOURNEY, '03c-wizard-drag-to-tab');

		// Changing a field's type resets its config/default to what the new
		// type expects, rather than carrying the old type's shape across (a
		// `model` field's `{model_type, placeholder}` config and string
		// filename default surviving into a `lora_picker` as a broken hybrid).
		const checkpointCard = wizard.locator('.di-field-card[data-field-name="checkpoint"]');
		await expect(checkpointCard).toHaveCount(1);
		await checkpointCard.locator('.di-field-type').selectOption('lora_picker');
		await screenshot(page, JOURNEY, '03d-wizard-field-type-reset');

		// Re-typing an integer field's default through the wizard's own text
		// input must reach the emitted preset as a native int, not the string
		// the input hands back - a wizard-authored default of "4" once
		// survived verbatim into generation.yml as `default: '4'`, which
		// preset lint rejects outright (see schema._typed_default).
		const stepsCard = wizard.locator('.di-field-card[data-field-name="steps"]');
		await expect(stepsCard).toHaveCount(1);
		await stepsCard.locator('.di-field-type').selectOption('integer');
		await stepsCard.locator('.di-field-default').fill('4');
		await expect(stepsCard.locator('.di-field-default')).toHaveValue('4');

		// The field editor's Configuration section is generated from
		// `/api/fields/types`'s `configuration_schema`, not a hand-written
		// per-type form - a slider's min/max/step controls and a select's
		// options line editor both come from the same contract the backend
		// declares.
		const cfgCard = wizard.locator('.di-field-card[data-field-name="cfg"]');
		await expect(cfgCard).toHaveCount(1);
		await cfgCard.locator('.di-field-type').selectOption('slider');
		await cfgCard.locator('button[data-action="toggle-mapping"]').click();
		await cfgCard.locator('[data-config-row="min"] input').fill('1');
		await cfgCard.locator('[data-config-row="max"] input').fill('20');
		await cfgCard.locator('[data-config-row="step"] input').fill('0.5');
		await screenshot(page, JOURNEY, '03e-wizard-slider-config');

		// "Sampler" is already a `select` field (node_catalog.yml) with a
		// live-file-backed `options` config - re-declaring its type here
		// proves the generic type-select path is idempotent, then the line
		// editor replaces its options outright.
		const samplerCard = wizard.locator('.di-field-card[data-field-name="sampler_name"]');
		await expect(samplerCard).toHaveCount(1);
		await samplerCard.locator('.di-field-type').selectOption('select');
		await samplerCard.locator('button[data-action="toggle-mapping"]').click();
		await samplerCard.locator('[data-config-row="options"] textarea').fill('Euler\nDPM++ 2M');
		await screenshot(page, JOURNEY, '03f-wizard-select-options');

		await wizard.locator('#import-model-family').fill(familyId);
		await wizard.locator('#import-display-name').fill('E2E imported SDXL');
		await wizard.locator('button[data-import-continue-form]').click();

		// Step 3: History - default_history pre-populates the table; the live
		// preview mirrors it.
		await expect(wizard.locator('[data-wiz-step="history"].current')).toBeVisible({ timeout: 10000 });
		await expect(wizard.locator('[data-history-preview]')).toBeVisible();
		await screenshot(page, JOURNEY, '04-wizard-history');
		await wizard.locator('button[data-import-continue-history]').click();

		// Step 4: Requirements (non-blocking - Continue works regardless of
		// what the checkers found against this backend-less throwaway instance).
		await expect(wizard.locator('[data-wiz-step="requirements"].current')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '05-wizard-requirements');
		await wizard.locator('button[data-import-create]').click();

		// Step 5: Done.
		await expect(wizard.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '06-wizard-done');
		await expect(wizard.locator('[data-import-lint]')).toContainText('imported');

		// The picked icon + display mode landed on the emitted preset's tab -
		// `configuration.icon`/`icon_display` (see backend/preset_import/emit.py).
		const formYml = readFileSync(resolve(createdPresetDir, 'imported/modes/txt2img/form.yml'), 'utf-8');
		expect(formYml).toContain('icon: lora');
		expect(formYml).toContain('icon_display: icon_label');

		// The "checkpoint" field switched to `lora_picker` above: its emitted
		// tab YAML must carry the fresh picker shape (an EMPTY list default -
		// the workflow's own file is never seeded - and max_items), never the
		// `model` type's leftover `options`/scalar-string config it replaced.
		const tabsDir = resolve(createdPresetDir, 'imported/modes/txt2img/tabs');
		const tabYamls = readdirSync(tabsDir).map((f) => readFileSync(resolve(tabsDir, f), 'utf-8'));
		const checkpointTabYml = tabYamls.find((y) => y.includes('name: checkpoint'));
		expect(checkpointTabYml, 'no tab file defines the "checkpoint" field').toBeTruthy();
		const checkpointFieldYml = extractFieldBlock(checkpointTabYml!, 'checkpoint');
		expect(checkpointFieldYml).toContain('type: lora_picker');
		expect(checkpointFieldYml).toContain('max_items: 6');
		expect(checkpointFieldYml).toMatch(/default:\s*\[\]/);
		expect(checkpointFieldYml).not.toContain('models/loras/');
		expect(checkpointFieldYml).not.toContain('options');

		// The "steps" field's re-typed default (integer, text "4") landed as
		// a native int - not the string a wizard regression once wrote.
		const stepsTabYml = tabYamls.find((y) => y.includes('name: steps'));
		expect(stepsTabYml, 'no tab file defines the "steps" field').toBeTruthy();
		const stepsFieldYml = extractFieldBlock(stepsTabYml!, 'steps');
		expect(stepsFieldYml).toContain('type: integer');
		expect(stepsFieldYml).toMatch(/default: 4\s*$/m);

		// The "cfg" field's min/max/step, set through the schema-generated
		// slider controls, landed on the emitted field's `configuration:`.
		const cfgTabYml = tabYamls.find((y) => y.includes('name: cfg'));
		expect(cfgTabYml, 'no tab file defines the "cfg" field').toBeTruthy();
		const cfgFieldYml = extractFieldBlock(cfgTabYml!, 'cfg');
		expect(cfgFieldYml).toContain('type: slider');
		expect(cfgFieldYml).toMatch(/min: 1(\.0)?\s*$/m);
		expect(cfgFieldYml).toMatch(/max: 20(\.0)?\s*$/m);
		expect(cfgFieldYml).toMatch(/step: 0\.5\s*$/m);

		// The "sampler_name" field's two lines typed into the select's
		// options line editor replaced its ~60-entry live sampler list
		// outright, landing as a plain scalar `options:` list.
		const samplerTabYml = tabYamls.find((y) => y.includes('name: sampler_name'));
		expect(samplerTabYml, 'no tab file defines the "sampler_name" field').toBeTruthy();
		const samplerFieldYml = extractFieldBlock(samplerTabYml!, 'sampler_name');
		expect(samplerFieldYml).toContain('type: select');
		expect(samplerFieldYml).toMatch(/options:\s*\n\s*-\s*Euler\s*\n\s*-\s*DPM\+\+ 2M/);

		const openLink = wizard.locator('a[data-import-open-preset]');
		await expect(openLink).toBeVisible();
		await openLink.click();

		await expect(page).toHaveURL(/tab=presets/);
		await page.waitForTimeout(500);
		await screenshot(page, JOURNEY, '07-opened-in-presets');
		await expect(page.getByText('E2E imported SDXL', { exact: false }).first()).toBeVisible({ timeout: 10000 });

		// The "Imported presets" tab lists the same preset back on the plugin page.
		await page.goto('/admin?tab=plugins');
		await page.waitForTimeout(500);
		await pluginListRow.click();
		await tabNav.getByText('Imported presets', { exact: false }).click();
		// Other leftover imported presets may already exist in this checkout's
		// content/presets/local/ (a prior run's cleanup, or a hand-imported
		// one) - scope every row action to THIS run's own row (unique family
		// id) rather than the whole table.
		const importedTable = page.locator('[data-imported-presets]');
		await expect(importedTable).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '08-imported-presets-tab');
		await expect(page.getByText('E2E imported SDXL', { exact: false }).first()).toBeVisible();
		const myRow = importedTable.locator('.ip-row').filter({ hasText: familyId });
		await expect(myRow).toHaveCount(1);

		// Reload: re-emits from the stored source under the same id, shows an
		// inline lint-result chip.
		await myRow.locator('button[aria-label="Reload from source"]').click();
		await expect(myRow.locator('.ip-reload-result')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '09-reload-result');

		// Modify: edit hands off to the "Import workflow" tab, prefilled from
		// this preset's stored source + its sidecar's `form`/`history`.
		await myRow.locator('button[aria-label="Edit"]').click();
		await expect(wizard).toBeVisible();
		await expect(wizard.locator('[data-import-editing]')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '10-wizard-edit-prefill');

		await wizard.locator('button[data-import-analyze]').click();
		await expect(wizard.locator('[data-wiz-step="form"].current')).toBeVisible();
		await wizard.locator('#import-display-name').fill('E2E imported SDXL (updated)');
		await wizard.locator('button[data-import-continue-form]').click();
		await expect(wizard.locator('[data-wiz-step="history"].current')).toBeVisible({ timeout: 10000 });
		await wizard.locator('button[data-import-continue-history]').click();
		await expect(wizard.locator('[data-wiz-step="requirements"].current')).toBeVisible({ timeout: 10000 });

		const updateBtn = wizard.locator('button[data-import-create]');
		await expect(updateBtn).toContainText('Update preset');
		await updateBtn.click();
		await expect(wizard.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '11-wizard-updated');

		// Back on Imported presets: the rename stuck (same preset, same id),
		// then delete it.
		await tabNav.getByText('Imported presets', { exact: false }).click();
		await expect(page.getByText('E2E imported SDXL (updated)', { exact: false })).toBeVisible({ timeout: 10000 });
		const myRowAfterUpdate = importedTable.locator('.ip-row').filter({ hasText: familyId });
		await expect(myRowAfterUpdate).toHaveCount(1);

		await myRowAfterUpdate.locator('button[aria-label="Delete"]').click();
		const confirmDialog = page.locator('[role="dialog"][aria-label="Delete imported preset"]');
		await expect(confirmDialog).toBeVisible();
		await screenshot(page, JOURNEY, '12-delete-confirm');
		await confirmDialog.getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(importedTable.locator('.ip-row').filter({ hasText: familyId })).toHaveCount(0, { timeout: 10000 });
		await screenshot(page, JOURNEY, '13-deleted');
	} finally {
		rmSync(createdPresetDir, { recursive: true, force: true });
	}
});

test('Admin > Plugins > ComfyUI Backend - "Add LoRA picker" wires a picker into a workflow with no LoRA nodes', async ({
	page
}) => {
	test.setTimeout(60_000);
	const familyId = `e2e-import-nolora-${Date.now()}`;
	const createdPresetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);

	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);

		await apiPost(page, '/api/plugins/scan', token);
		const pluginsList = await apiGet(page, '/api/plugins', token);
		const pluginRow = (pluginsList.data || []).find((p: any) => p.id === PLUGIN_ID);
		if (!pluginRow) {
			test.skip(true, `'${PLUGIN_ID}' was not discovered on this throwaway instance.`);
			return;
		}
		if (!pluginRow.enabled) {
			await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
		}

		await page.goto('/admin?tab=plugins');
		await page.waitForTimeout(500);

		const pluginListRow = page.getByText('ComfyUI Backend', { exact: false }).first();
		if ((await pluginListRow.count()) === 0) {
			test.skip(
				true,
				"'ComfyUI Backend' is not present in the plugin list - the plugin frontend isn't mounted on this build."
			);
			return;
		}
		await pluginListRow.click();

		const tabNav = page.locator('nav[aria-label="Plugin details"]');
		await expect(tabNav).toBeVisible();
		const importTab = tabNav.getByText('Import workflow', { exact: false });
		if ((await importTab.count()) === 0) {
			test.skip(true, "'Import workflow' tab not present - the admin_tabs hook wasn't picked up on this build.");
			return;
		}
		await importTab.click();

		const wizard = page.locator('[data-import-wizard]');
		await expect(wizard).toBeVisible();

		// sdxl_basic_api.json (same fixture as the journey above) has a
		// KSampler fed straight off CheckpointLoaderSimple - no LoRA node at
		// all, but a sampler cluster for `analysis.model_chain` to point at.
		const workflowJson = readFileSync(FIXTURE_PATH, 'utf-8');
		await wizard.locator('textarea[data-import-json-input]').fill(workflowJson);
		await wizard.locator('button[data-import-analyze]').click();

		await expect(wizard.locator('[data-import-form-inputs]')).toBeVisible({ timeout: 10000 });

		const loraChainCard = wizard.locator('[data-import-lora-chain]');
		await expect(loraChainCard).toBeVisible({ timeout: 10000 });
		await expect(loraChainCard).toHaveAttribute('data-import-lora-chain-empty', '');
		await screenshot(page, JOURNEY, '14-no-lora-card');

		const fieldsList = wizard.locator('[data-import-form-items] .di-field-card');
		const fieldCountBefore = await fieldsList.count();

		const addPickerBtn = loraChainCard.locator('button[data-action="add-lora-picker"]');
		await addPickerBtn.click();
		await expect(fieldsList).toHaveCount(fieldCountBefore + 1);
		await expect(wizard.locator('[data-import-form-items] .di-field-card[data-field-name="loras"]')).toBeVisible();
		await expect(addPickerBtn).toBeDisabled();
		await screenshot(page, JOURNEY, '15-lora-picker-added-no-chain');

		await wizard.locator('#import-model-family').fill(familyId);
		await wizard.locator('#import-display-name').fill('E2E no-LoRA import');
		await wizard.locator('button[data-import-continue-form]').click();

		await expect(wizard.locator('[data-wiz-step="history"].current')).toBeVisible({ timeout: 10000 });
		await wizard.locator('button[data-import-continue-history]').click();

		await expect(wizard.locator('[data-wiz-step="requirements"].current')).toBeVisible({ timeout: 10000 });
		await wizard.locator('button[data-import-create]').click();

		await expect(wizard.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });

		// The emitter splices the picker's `@loop` at the model chain's tail
		// since nothing was replaced (no chain existed to replace) - a plain
		// model-chain-only splice has no CLIP path, so it's LoraLoaderModelOnly.
		const pipelineYml = readFileSync(resolve(createdPresetDir, 'imported/modes/txt2img/pipeline.yml'), 'utf-8');
		expect(pipelineYml).toContain('@loop');
		expect(pipelineYml).toContain('LoraLoaderModelOnly');
	} finally {
		rmSync(createdPresetDir, { recursive: true, force: true });
	}
});
