import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';
import { SWITCH, assignMoss, mockPresetVars, openMoss } from './regionalHelpers';

const JOURNEY = 'regional-prompt-chat';
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

const REGIONS = {
	enabled: true,
	items: [
		{ x: 0.1, y: 0.05, w: 0.8, h: 0.17, caption: 'Title "EXPLORE THE COAST" in bold cream letters' },
		{ x: 0.1, y: 0.35, w: 0.4, h: 0.5, caption: 'A weathered stone lighthouse on a basalt cliff' }
	]
};

const pendingExecution = {
	tool_name: 'set_regional_prompt',
	arguments: {},
	result: { success: true, data: JSON.stringify({ status: 'pending_approval' }) },
	duration_ms: 0,
	approval_status: 'pending',
	preview: {
		action: 'Set regional prompt',
		target: '2 regions',
		items: [],
		kind: 'regions',
		summary: 'Set regional prompt',
		rows: [
			{ position: '10%, 5%', size: '80% × 17%', text: REGIONS.items[0].caption },
			{ position: '10%, 35%', size: '40% × 50%', text: REGIONS.items[1].caption }
		]
	}
};

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

function sse(events: Array<{ type: string; data: unknown }>): string {
	return events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`).join('');
}

async function mockStream(page: Page) {
	let calls = 0;
	await page.route('**/api/chat/sessions/*/messages/stream', async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		calls += 1;
		const now = new Date().toISOString();
		const first = calls === 1;
		const content = first ? 'I laid out the poster as two regions.' : 'The regions are in place.';
		await route.fulfill({
			status: 200,
			contentType: 'text/event-stream',
			headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' },
			body: sse([
				{ type: 'message_created', data: { user_message_id: 'mock-user' } },
				{ type: 'token', data: { content } },
				{
					type: 'done',
					data: {
						user_message: { id: 'mock-user', created_at: now },
						assistant_message: {
							id: first ? 'mock-assistant' : 'mock-assistant-2',
							role: 'assistant',
							content,
							created_at: now,
							tool_executions: first ? [pendingExecution] : []
						}
					}
				}
			])
		});
	});
	await page.route('**/api/chat/sessions/*/tools/resolve', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				success: true,
				data: {
					status: 'approved',
					message_id: 'mock-assistant',
					tool_index: 0,
					execution: {
						...pendingExecution,
						approval_status: 'approved',
						result: {
							success: true,
							data: JSON.stringify({ action: 'set_regional_prompt', regional_prompt: REGIONS })
						}
					}
				}
			})
		});
	});
}

async function ask(page: Page, text: string) {
	const composer = page.locator('.composer:has(button[aria-label="Send message"])');
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await expect(chipInput).toHaveAttribute('contenteditable', 'true');
	await expect(async () => {
		await chipInput.click();
		await page.keyboard.press('ControlOrMeta+a');
		await page.keyboard.type(text);
		await expect(chipInput).toContainText(text, { timeout: 2000 });
	}).toPass({ timeout: 20000 });
	await composer.locator('button[aria-label="Send message"]').click();
}

for (const viewport of [
	{ label: '1440', width: 1440, height: 900 },
	{ label: '390', width: 390, height: 844 }
] as const) {
	test.describe(`regional chat at ${viewport.label}px`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('approval card lists the regions', async ({ page }) => {
			test.setTimeout(120000);
			await page.emulateMedia({ reducedMotion: 'reduce' });
			await loginAsOwner(page);
			const token = await ownerToken(page);
			await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
			await mockStream(page);
			const phone = viewport.width < 768;
			if (phone) {
				await page.goto('/generate');
				await page.getByRole('button', { name: 'Open AI chat' }).click();
			} else {
				await page.goto('/models');
				await page.getByRole('button', { name: 'AI Chat' }).click();
			}
			await expect(page.locator('.composer:has(button[aria-label="Send message"])')).toBeVisible({ timeout: 15000 });
			await ask(page, 'Lay out a poster with a title and a lighthouse');
			const card = page.locator('[data-testid="action-card"]').last();
			await expect(card).toBeVisible({ timeout: 30000 });
			const rows = card.locator('[data-testid="action-regions"] .action-timeline-row');
			await expect(rows).toHaveCount(2);
			await expect(rows.nth(0)).toContainText('10%, 5% · 80% × 17%');
			await expect(rows.nth(0)).toContainText('EXPLORE THE COAST');
			const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
			expect(overflow).toBeLessThanOrEqual(0);
			await screenshot(page, JOURNEY, `approval-card-${viewport.label}`);
		});
	});
}

test.describe('regional chat round trip', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('approving the tool puts the regions into the Regional view', async ({ page }) => {
		test.setTimeout(180000);
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
		const name = await assignMoss(page);
		test.skip(!name, 'No MOSS-Image preset on this instance');
		await mockPresetVars(page, true);
		await mockStream(page);
		await openMoss(page, name!);
		await expect(page.locator(SWITCH)).toBeVisible({ timeout: 20000 });
		await page.getByRole('button', { name: 'AI Chat' }).click();
		await expect(page.locator('.composer:has(button[aria-label="Send message"])')).toBeVisible({ timeout: 15000 });
		await ask(page, 'Lay out a poster with a title and a lighthouse');
		const card = page.locator('[data-testid="action-card"]').last();
		await expect(card).toBeVisible({ timeout: 30000 });
		await card.locator('.action-btn.is-approve').click();
		await expect(page.locator('[data-testid="regional-editor"]')).toBeVisible({ timeout: 15000 });
		await expect(page.locator('[data-region]')).toHaveCount(2);
		await expect(page.locator(SWITCH)).toContainText('2');
		await screenshot(page, JOURNEY, 'round-trip-applied-1440');
	});
});
