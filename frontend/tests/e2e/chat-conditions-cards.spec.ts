import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-conditions-cards';
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';
const BLOCK = '<<if $age > 65>>grey hair<<else>>auburn hair<<end>>';

const VIEWPORTS = [
	{ label: '1440', width: 1440, height: 900 },
	{ label: '390', width: 390, height: 844 }
] as const;

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

interface MockedExecution {
	tool_name: string;
	arguments: Record<string, unknown>;
	result: { success: boolean; data: string };
	duration_ms: number;
	approval_status: 'pending';
	preview?: Record<string, unknown>;
}

function sse(events: Array<{ type: string; data: unknown }>): string {
	return events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`).join('');
}

async function mockReply(page: Page, content: string, toolExecutions: MockedExecution[] = []) {
	await page.route('**/api/chat/sessions/*/messages/stream', async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		const now = new Date().toISOString();
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
							id: 'mock-assistant',
							role: 'assistant',
							content,
							created_at: now,
							tool_executions: toolExecutions
						}
					}
				}
			])
		});
	});
}

async function openChat(page: Page) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	if (phone) {
		await page.goto('/generate');
		await page.getByRole('button', { name: 'Open AI chat' }).click();
	} else {
		await page.goto('/models');
		await page.getByRole('button', { name: 'AI Chat' }).click();
	}
	const composer = page.locator('.composer:has(button[aria-label="Send message"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	return composer;
}

async function expectFitsViewport(page: Page, card: Locator) {
	const width = page.viewportSize()!.width;
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow, 'page has no horizontal scroll').toBeLessThanOrEqual(0);
	const box = await card.boundingBox();
	expect(box, 'card is laid out').not.toBeNull();
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
	for (const chip of await card.locator('.condition-chip').all()) {
		const chipBox = await chip.boundingBox();
		expect(chipBox!.x + chipBox!.width, 'chip stays inside the card').toBeLessThanOrEqual(box!.x + box!.width + 1);
	}
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

function variableCard(oldValue: string, newValue: string): MockedExecution {
	return {
		tool_name: 'manage_prompt_variables',
		arguments: {},
		result: {
			success: true,
			data: JSON.stringify({
				status: 'pending_approval',
				proposed_changes: [{ field_name: '${age}', old_value: oldValue, new_value: newValue }],
				change_count: 1
			})
		},
		duration_ms: 0,
		approval_status: 'pending'
	};
}

function savedSegmentCard(newText: string, oldText: string): MockedExecution {
	return {
		tool_name: 'update_saved_segment',
		arguments: {},
		result: { success: true, data: JSON.stringify({ action: 'update_saved_segment', message: 'pending' }) },
		duration_ms: 0,
		approval_status: 'pending',
		preview: {
			action: 'Update saved segment',
			target: 'Aged portrait',
			items: [],
			kind: 'text_edit',
			summary: 'Aged portrait',
			fields: [],
			text_blocks: [{ label: 'Content', text: newText, old_text: oldText }]
		}
	};
}

for (const viewport of VIEWPORTS) {
	test.describe(`at ${viewport.label}px`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('a version card shows the condition as a chip and keeps Apply on', async ({ page }) => {
			test.setTimeout(120000);
			await openChat(page);
			await mockReply(
				page,
				`Here is a version with the condition:\n<tool_action type="add_segment">portrait of a woman, ${BLOCK}, soft light</tool_action>`
			);
			await ask(page, 'Make her grey when she is over 65');

			const card = page.locator('.prompt-card').last();
			await expect(card).toBeVisible({ timeout: 30000 });
			await expect(card.locator('.condition-chip')).toHaveCount(1);
			await expect(card).not.toContainText('<<if');
			await expect(card.locator('[data-testid="condition-tag-problems"]')).toHaveCount(0);
			await expect(card.locator('[data-testid="segment-action-apply"]')).toBeEnabled();
			await card.locator('[data-testid="segment-action-apply"]').scrollIntoViewIfNeeded();
			await expect(card.locator('[data-testid="segment-action-apply"]')).toBeInViewport();
			await expectFitsViewport(page, card);
			await screenshot(page, JOURNEY, `version-card-${viewport.label}`);
		});

		test('a malformed condition names the problem and turns Apply off', async ({ page }) => {
			test.setTimeout(120000);
			await openChat(page);
			await mockReply(
				page,
				'Try this:\n<tool_action type="add_segment">portrait, <<if $age > 65>>grey hair, soft light</tool_action>'
			);
			await ask(page, 'Make her grey when she is over 65');

			const card = page.locator('.prompt-card').last();
			const problems = card.locator('[data-testid="condition-tag-problems"]');
			await expect(problems).toBeVisible({ timeout: 30000 });
			await expect(problems).toContainText('Apply is off');
			await expect(problems).toContainText('never reaches');
			const apply = card.locator('[data-testid="segment-action-apply"]');
			await expect(apply).toBeDisabled();
			await expect(apply).toHaveCSS('opacity', '0.5');
			await expect(apply).toHaveCSS('cursor', 'not-allowed');
			await apply.scrollIntoViewIfNeeded();
			await expect(apply).toBeInViewport();
			await expectFitsViewport(page, card);
			await screenshot(page, JOURNEY, `malformed-card-${viewport.label}`);
		});

		test('approval cards show a number variable and a condition properly', async ({ page }) => {
			test.setTimeout(120000);
			await openChat(page);
			await mockReply(page, 'I proposed an age variable and a condition for the saved segment.', [
				variableCard(
					'number from 18 to 90 in steps of 1 — shuffles each generation',
					'number from 18 to 90 in steps of 1 — pinned to 70'
				),
				savedSegmentCard(`portrait of a woman, ${BLOCK}`, 'portrait of a woman, auburn hair')
			]);
			await ask(page, 'Pin the age at 70 and add the grey hair condition');

			const cards = page.locator('[data-testid="action-card"]');
			await expect(cards).toHaveCount(2, { timeout: 30000 });
			const variable = cards.nth(0);
			const segment = cards.nth(1);

			const values = variable.locator('[data-testid="number-variable-value"]');
			await expect(values).toHaveCount(2);
			await expect(values.nth(1)).toContainText('18–90');
			await expect(values.nth(1)).toContainText('Pinned');
			await expect(values.nth(1)).toContainText('70');
			await expect(values.nth(0)).toContainText('Shuffles each generation');

			await expect(segment.locator('.action-copy:not(.is-old) .condition-chip')).toHaveCount(1);
			await expect(segment).not.toContainText('<<if');
			await expectFitsViewport(page, variable);
			await expectFitsViewport(page, segment);
			await screenshot(page, JOURNEY, `approval-cards-${viewport.label}`);
		});
	});
}
