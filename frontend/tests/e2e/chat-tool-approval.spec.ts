import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { startFakeLLM, seedFakeLlmConfig, type FakeLLMServer } from './fake-llm';

const JOURNEY = 'chat-tool-approval';
const BEAT = 450;
const BACKEND = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8055';

// An approval-gated tool call surfaces in the docked ApprovalDock above the
// composer (tool label · action · target · items + Approve/Reject), not
// inline in the transcript, and resolving it either way continues the
// conversation with a narrated outcome instead of dead-ending. The LLM is a
// scripted fake OpenAI server: turn 1 emits a create_phrasebook_category
// tool_call (streamed tool_calls fragments), the post-approval narration
// turn is plain text.

let fake: FakeLLMServer;

test.beforeEach(async () => {
	fake = await startFakeLLM();
});

test.afterEach(async () => {
	await fake.close();
});

async function openChatPanel(page: Page) {
	await page.goto('/models');
	// Sidebar.svelte's AI Chat trigger has no `title` attribute (its tooltip
	// text comes from a wrapping <Tooltip>, not a native title) — only its
	// accessible name, "AI Chat" (via aria-label), identifies it.
	const fab = page.getByRole('button', { name: 'AI Chat' });
	await expect(fab).toBeVisible({ timeout: 15000 });
	await fab.click();
	const composer = page.locator('.bg-surface-1.rounded-lg:has(button[title="Send (Enter)"])');
	await expect(composer).toBeVisible({ timeout: 15000 });
	return composer;
}

async function sendMessage(page: Page, composer: ReturnType<Page['locator']>, text: string) {
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await chipInput.click();
	await page.keyboard.type(text);
	await composer.locator('button[title="Send (Enter)"]').click();
}

test('approval-gated tool docks above the composer; approve and reject both continue the conversation', async ({
	page
}) => {
	test.setTimeout(120000);
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await seedFakeLlmConfig(page.request, BACKEND, token, fake.url);

	// The tool call targets 'camera.angles'; its parent category must exist for
	// the proposal to reach the pending_approval path.
	const parent = await page.request.post(`${BACKEND}/api/phrasebook/categories`, {
		headers: { Authorization: `Bearer ${token}` },
		data: { name: 'camera', path: 'camera', description: '' }
	});
	expect(parent.ok(), `parent category create -> ${parent.status()}`).toBeTruthy();

	const composer = await openChatPanel(page);

	// --- Pass 1: Approve ---
	fake.enqueue({
		kind: 'tool_call',
		name: 'create_phrasebook_category',
		arguments: { path: 'camera.angles', name: 'Angles', description: 'Camera angle vocabulary' }
	});
	await sendMessage(page, composer, 'Save camera angles as a phrasebook category');

	const card = page.locator('div.border-warning\\/35');
	await expect(card).toBeVisible({ timeout: 30000 });

	// The dock states intent: tool label + action + target + items + explicit actions.
	await expect(card).toContainText('Create Phrasebook Category');
	await expect(card).toContainText('Create category');
	await expect(card).toContainText('under camera');
	await expect(card).toContainText('camera.angles');
	await expect(card).toContainText('from reply');
	const approveButton = card.getByRole('button', { name: 'Approve' });
	const rejectButton = card.getByRole('button', { name: 'Reject' });
	await expect(approveButton).toBeVisible();
	await expect(rejectButton).toBeVisible();

	// The composer is gated while an approval is pending.
	const chipInput = composer.locator('[role="textbox"][aria-placeholder]');
	await expect(chipInput).toHaveAttribute('aria-placeholder', 'Resolve approvals to continue…');

	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'approval-card-pending');

	fake.enqueue({
		kind: 'text',
		text: 'Created the camera.angles category. New markers can use #camera.angles.'
	});
	await approveButton.click();

	// Card resolves away and the conversation continues with the outcome message.
	await expect(card).toBeHidden({ timeout: 30000 });
	await expect(page.getByText('Created the camera.angles category')).toBeVisible({
		timeout: 30000
	});
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'after-approve');

	// The approved action actually ran on the backend.
	const categories = await page.request.get(`${BACKEND}/api/phrasebook/categories`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	expect(categories.ok()).toBeTruthy();
	const categoriesText = JSON.stringify(await categories.json());
	expect(categoriesText, 'camera.angles exists after approve').toContain('camera.angles');

	// --- Pass 2: Deny (fresh session) ---
	await page.locator('button[title="New chat"]').click();
	await page.waitForTimeout(BEAT);

	fake.enqueue({
		kind: 'tool_call',
		name: 'create_phrasebook_category',
		arguments: { path: 'camera.moves', name: 'Moves', description: 'Camera move vocabulary' }
	});
	await sendMessage(page, composer, 'Also save camera moves as a category');

	await expect(card).toBeVisible({ timeout: 30000 });
	await expect(card).toContainText('Create category');
	await expect(card).toContainText('camera.moves');
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'deny-card-pending');

	fake.enqueue({
		kind: 'text',
		text: 'Understood - I did not create the camera.moves category.'
	});
	await card.getByRole('button', { name: 'Reject' }).click();

	await expect(card).toBeHidden({ timeout: 30000 });
	await expect(page.getByText('I did not create the camera.moves category')).toBeVisible({
		timeout: 30000
	});
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, 'after-deny');

	const categoriesAfterDeny = await page.request.get(`${BACKEND}/api/phrasebook/categories`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	const afterDenyText = JSON.stringify(await categoriesAfterDeny.json());
	expect(afterDenyText, 'camera.moves must NOT exist after deny').not.toContain('camera.moves');

	await screenshot(page, JOURNEY, 'conversation-final');

	console.log(
		`[${JOURNEY}] approve: card (action+target+items) -> resolved -> narrated outcome -> category created; ` +
			`deny: card -> rejected -> narrated acknowledgement -> category NOT created; ` +
			`fake-llm requests=${fake.requests.length}, unconsumed turns=${fake.pending()}`
	);
});
