import { test, expect, type Locator, type Page } from '@playwright/test';
import { screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';
import {
	api,
	brewStatus,
	headers,
	openElixir,
	prepare,
	problems,
	removeContracts,
	writeContracts,
	FAKE_PRESET
} from './elixirs-fixtures';

const JOURNEY = 'elixirs-writer';
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const INSTRUCTION = 'For the provided image write a prompt that recreates it';
const WRITTEN = [
	'A small glass potion bottle glowing amber on a dark oak table, soft rim light, shallow depth of field',
	'A violet potion in a round flask on a mossy stone ledge, cool moonlight, faint mist'
];

const CONTRACTS = `schema_version: 1
presets:
  "${FAKE_PRESET}":
    name: "Fake Studio"
    modes:
      txt2img:
        label: "Text to image"
        prompt: true
        quantity_field: "count"
        outputs:
          images:
            label: "Final images"
            media_type: "image"
`;

let fake: FakeLLMServer;

async function seedSeeingLlm(page: Page, token: string) {
	const configId = await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
	await api(page, token, 'put', `/api/llm/configurations/${configId}`, {
		id: configId,
		name: 'e2e-fake-openai',
		type: 'openai-compatible',
		enabled: true,
		base_url: fake.url,
		model: 'fake',
		system_message: 'You are a test assistant.',
		temperature: 0.2,
		max_tokens: 512,
		timeout: 30,
		supports_vision: true
	});
}

async function still(page: Page, token: string, name: string, from: string, to: string) {
	const base64 = await page.evaluate(
		([start, end]) => {
			const canvas = document.createElement('canvas');
			canvas.width = 96;
			canvas.height = 96;
			const context = canvas.getContext('2d')!;
			const gradient = context.createLinearGradient(0, 0, 96, 96);
			gradient.addColorStop(0, start);
			gradient.addColorStop(1, end);
			context.fillStyle = gradient;
			context.fillRect(0, 0, 96, 96);
			return canvas.toDataURL('image/png').split(',')[1];
		},
		[from, to]
	);
	const buffer = Buffer.from(base64, 'base64');
	const upload = await page.request.post('/api/media/upload', { headers: headers(token), multipart: { file: { name, mimeType: 'image/png', buffer } } });
	expect(upload.ok(), `upload ${name} -> ${upload.status()}`).toBeTruthy();
	const listed = await api(page, token, 'get', '/api/media/uploads?media_type=image&limit=50');
	const item = listed.data.uploads.find((entry: any) => entry.original_filename === name);
	expect(item, `${name} is not in the library`).toBeTruthy();
	return { kind: 'library', item_id: item.id, media_type: 'image', original_filename: name };
}

async function saved(editor: Locator) {
	await expect(editor.getByText('Saved', { exact: true })).toBeVisible({ timeout: 15000 });
}

async function themed(page: Page, theme: 'dark' | 'light', label: string) {
	await page.mouse.move(600, 860);
	await page.evaluate((value) => (document.documentElement.dataset.theme = value), theme);
	await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
	await screenshot(page, JOURNEY, `${label}-${theme}`);
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
	writeContracts(CONTRACTS);
	fake = await startFakeLLM();
});

test.afterAll(async () => {
	removeContracts();
	await fake?.close();
});

test('a prompt writer looks at each image and its prompts reach the preset', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await seedSeeingLlm(page, token);
	fake.enqueue(...WRITTEN.map((text) => ({ kind: 'text' as const, text })));
	const amber = await still(page, token, 'potion-amber.png', 'rgb(200, 130, 40)', 'rgb(60, 30, 10)');
	const violet = await still(page, token, 'potion-violet.png', 'rgb(140, 80, 200)', 'rgb(20, 40, 60)');
	const session = await api(page, token, 'post', '/api/sessions/save', {
		preset_id: FAKE_PRESET,
		name: 'Potion render',
		data: { txt2img: { prompt: 'a potion', formData: { model: modelRef, count: 1, aspect_ratio: '1:1' } } }
	});
	const created = await api(page, token, 'post', '/api/elixirs', {
		name: 'Describe and render',
		run_mode: 'automatic',
		definition: {
			nodes: [
				{ id: 'photos', kind: 'media', title: 'Potion photos', position: { x: 40, y: 80 }, media_type: 'image', assets: [amber, violet] },
				{ id: 'render', kind: 'preset', title: 'Potion render', position: { x: 1420, y: 80 }, preset_id: FAKE_PRESET, mode: 'txt2img' }
			],
			edges: []
		}
	});
	await api(page, token, 'post', `/api/elixirs/${created.id}/nodes/render/session`, {
		expected_revision: created.revision,
		session_id: session.data.id
	});

	const editor = await openElixir(page, created.id);
	const render = editor.getByRole('article', { name: 'Potion render, preset step' });
	await expect(render).toBeVisible({ timeout: 30000 });
	await render.getByRole('button', { name: /^Prompt: a potion/ }).click();
	await page.getByRole('menu', { name: 'Prompt' }).getByRole('menuitem', { name: /Write with AI/ }).click();
	const panel = editor.getByRole('complementary', { name: /settings$/ });
	await expect(panel.getByText('prompt writer', { exact: true })).toBeVisible();
	await panel.getByLabel('Name').fill('Describe');
	await panel.getByLabel('Instruction').fill(INSTRUCTION);
	await panel.getByLabel('Name').press('Enter');
	await expect(panel.getByText('Follows the prompt style of Potion render and the model in its session.')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);
	const writer = editor.getByRole('article', { name: 'Describe, prompt writer' });
	await expect(writer.getByText(INSTRUCTION)).toBeVisible();
	await expect(render.getByRole('button', { name: /^Prompt: Describe/ })).toBeVisible();
	await expect(writer.locator('[data-card-id="render"]')).toHaveCount(0);
	await expect(editor.locator('[data-lane-node]').filter({ has: writer }).locator('[data-card-id="render"]')).toHaveCount(1);

	await writer.getByRole('button', { name: /^Looks at: Nothing: writes once/ }).click();
	const looks = page.getByRole('menu', { name: 'Looks at' });
	await expect(looks.getByRole('menuitem', { name: /Potion render/ })).toHaveAttribute('aria-disabled', 'true');
	await looks.getByRole('menuitemradio', { name: /Potion photos/ }).click();
	await expect(writer.getByRole('button', { name: /^Looks at: Potion photos/ })).toBeVisible();
	await saved(editor);
	await expect(problems(editor)).toContainText('0', { timeout: 15000 });
	await expect(editor.locator('[data-lane-node]').filter({ has: editor.getByRole('article', { name: 'Potion photos, files' }) }).locator('[data-card-id="render"]')).toHaveCount(1);
	await themed(page, 'dark', 'lane-ready');
	await themed(page, 'light', 'lane-ready');
	await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));

	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 120000 });
	await expect(writer.getByText('Wrote 2 prompts')).toBeVisible();
	await expect(render.getByText('Done · 2 images')).toBeVisible();
	const prompts = editor.getByRole('list', { name: 'Describe prompts' });
	await expect(prompts.getByRole('listitem')).toHaveCount(2);
	for (const text of WRITTEN) await expect(prompts.getByText(text)).toBeVisible();
	await expect(prompts.getByText('potion-amber.png', { exact: true })).toBeVisible();
	await expect(prompts.getByText('potion-violet.png', { exact: true })).toBeVisible();

	const asked = fake.requests.filter((request) => request.servedAs === 'queued-turn');
	expect(asked).toHaveLength(2);
	for (const request of asked) {
		const user = request.messages.find((message) => message.role === 'user');
		const parts = Array.isArray(user?.content) ? (user!.content as any[]) : [];
		expect(parts.some((part) => part.type === 'image_url'), 'the language model was shown the image').toBe(true);
		const words = parts.filter((part) => part.type === 'text').map((part) => part.text).join('\n');
		expect(words).toContain(INSTRUCTION);
		expect(words).toContain('Target preset:');
	}

	const runs = await api(page, token, 'get', `/api/elixirs/${created.id}/runs`);
	const detail = await api(page, token, 'get', `/api/elixirs/runs/${runs.runs[0].id}`);
	const generated = detail.invocations.filter((item: any) => item.node_id === 'render');
	expect(generated.map((item: any) => item.status)).toEqual(['completed', 'completed']);
	const received: string[] = [];
	for (const item of generated) {
		const generation = await api(page, token, 'get', `/api/generations/history/${item.generation_id}?include_files=false`);
		const body = JSON.stringify(generation);
		const match = WRITTEN.find((text) => body.includes(text));
		expect(match, `generation ${item.generation_id} got a written prompt`).toBeTruthy();
		expect(body).not.toContain('"a potion"');
		received.push(match!);
	}
	expect(received.sort()).toEqual([...WRITTEN].sort());
	await themed(page, 'dark', 'lane-brewed');
	await themed(page, 'light', 'lane-brewed');
	await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));

	await prompts.getByRole('button', { name: 'Pin potion-amber.png' }).click();
	await expect(prompts.getByRole('button', { name: 'Unpin potion-amber.png' })).toHaveAttribute('aria-pressed', 'true');
	await saved(editor);
	await writer.getByRole('button', { name: 'Describe settings' }).click();
	await expect(panel.getByText('Follows the prompt style of Potion render and the model in its session.')).toBeVisible();
	await expect(panel.getByLabel('Pinned prompts')).toContainText('1');
	await themed(page, 'dark', 'writer-settings');
	await themed(page, 'light', 'writer-settings');
	const elixir = await api(page, token, 'get', `/api/elixirs/${created.id}`);
	const stored = elixir.definition.nodes.find((node: any) => node.kind === 'llm');
	const amberKey = `library:${amber.item_id}`;
	const wroteForAmber = detail.invocations.find((item: any) => item.node_id === stored.id && item.input_key === amberKey);
	expect(stored.pinned).toEqual({ [amberKey]: wroteForAmber.outputs[0].text });
});
