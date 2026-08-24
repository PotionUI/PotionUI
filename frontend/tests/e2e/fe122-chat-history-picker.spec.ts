import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'fe122-chat-history-picker';
const BEAT = 400;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// Repro for "can't load image from history on the LLM Chat media loader
// field." Opens the global chat panel (a `translate-x-*` slide-over, the
// exact kind of transformed ancestor `use:portal` exists to escape), toggles
// the vision image panel, opens Generation History from the compact
// MediaLoaderField, picks a seeded generation, and asserts the picked image
// actually lands in the field - not just that the modal opened/closed.

const TINY_PNG_BASE64 =
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

async function seedVisionCapableLlmConfig(page: Page, token: string, fakeUrl: string) {
	const configId = await seedFakeLlmConfig(page.request, BACKEND, token, fakeUrl);
	// supports_vision isn't part of the shared seed payload - flip it with a
	// follow-up PUT carrying the same fields the helper already established.
	const put = await page.request.put(`${BACKEND}/api/llm/configurations/${configId}`, {
		headers: { Authorization: `Bearer ${token}` },
		data: {
			id: configId,
			name: 'e2e-fake-llm',
			type: 'openai',
			enabled: true,
			base_url: fakeUrl,
			model: 'fake',
			system_message: 'You are a test assistant.',
			temperature: 0.2,
			max_tokens: 512,
			timeout: 30,
			supports_vision: true
		}
	});
	expect(put.ok(), `LLM config vision update -> ${put.status()}`).toBeTruthy();
	return configId;
}

test('chat vision picker - History pick lands the image in the field', async ({ page }) => {
	test.setTimeout(60000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedVisionCapableLlmConfig(page, token, fake.url);

	// --- Seed a real generation to pick from History.
	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: { name: 'fe122-chat-history-pick.png', mimeType: 'image/png', buffer: pngBuffer }
		}
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();
	const generationId = (await uploadRes.json()).data.generation_id as string;

	// --- Open the chat panel (the slide-over with the transformed ancestor).
	await page.goto('/models');
	// Only its accessible name, "AI Chat" (via aria-label), identifies it —
	// see chat-composer.spec.ts for why not `title`.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();

	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });

	// --- Reveal the vision image panel.
	const attachImageButton = page.locator('button[title="Attach image"]');
	await expect(attachImageButton).toBeVisible({ timeout: 15000 });
	await attachImageButton.click();

	const compactDropHint = page.getByText(/Paste or drop image/i).first();
	await expect(compactDropHint).toBeVisible({ timeout: 15000 });
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '01-panel-open-empty');

	// --- Open History from the compact field and pick the seeded generation.
	const compactDropzone = compactDropHint.locator(
		'xpath=ancestor::*[contains(@class, "border-dashed")][1]'
	);
	await compactDropzone.getByTitle('History').click();

	const historyModal = page.getByText('Select Image from Generation History');
	await expect(historyModal).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '02-history-modal-open');

	const thumbnail = page.locator(`img[src*="${generationId}"]`).first();
	await expect(thumbnail, 'seeded generation should appear in the history grid').toBeVisible({
		timeout: 20000
	});
	await thumbnail.locator('xpath=ancestor::*[@role="button"][1]').click();

	await expect(historyModal).not.toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	// --- Decisive assertion: the compact field's preview shows the picked
	// image, not an empty dropzone.
	await expect(compactDropHint).not.toBeVisible();
	const previewImage = page.locator(`img[src*="${generationId}"]`).first();
	await expect(previewImage, 'vision-image field should render the picked image').toBeVisible({
		timeout: 20000
	});
	await screenshot(page, JOURNEY, '03-picked');

	console.log(`[${JOURNEY}] history pick rendered for generation ${generationId}`);
});
