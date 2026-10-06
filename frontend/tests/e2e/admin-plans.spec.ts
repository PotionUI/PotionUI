import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'admin-plans';

test('admin creates a plan with a daily limit, assigns it to a group and sees it on a member page', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const stamp = Date.now();
	const planName = `e2e-plan-${stamp}`;
	const groupName = `e2e-plan-group-${stamp}`;
	const username = `e2e-plan-user-${stamp}`;

	const groupCreate = await page.request.post('/api/user-groups', { headers, data: { name: groupName, description: null } });
	expect(groupCreate.ok(), `group create -> ${groupCreate.status()}`).toBeTruthy();
	const groupId = (await groupCreate.json()).data.id as string;

	const userCreate = await page.request.post('/api/users', {
		headers,
		data: { username, email: `${username}@example.com`, password: 'e2e-password-1', account_type: 'USER' }
	});
	expect(userCreate.ok(), `user create -> ${userCreate.status()}`).toBeTruthy();
	const userId = (await userCreate.json()).data.id as string;

	const addMember = await page.request.post(`/api/user-groups/${groupId}/members`, { headers, data: { user_ids: [userId] } });
	expect(addMember.ok(), `add member -> ${addMember.status()}`).toBeTruthy();

	await page.goto('/admin?tab=users&view=plans');
	await expect(page.getByRole('button', { name: 'New plan' })).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'New plan' }).click();

	await page.locator('#plan-name').fill(planName);
	await page.locator('[data-add-limit] button').click();
	await page.locator('[data-kind-option="generations_per_day"]').click();
	await page.locator('[data-limit-input="generations_per_day"]').fill('5');
	await screenshot(page, JOURNEY, 'plan-editor');
	await page.getByRole('button', { name: 'Create', exact: true }).click();

	await expect(page.locator('[data-limit-input="generations_per_day"]')).toHaveValue('5', { timeout: 15000 });
	await expect(page.getByRole('heading', { name: planName })).toBeVisible({ timeout: 15000 });

	await page.goto(`/admin?tab=users&view=groups&id=${groupId}`);
	const groupPlan = page.locator('[data-group-plan-select]');
	await expect(groupPlan).toBeVisible({ timeout: 15000 });
	await groupPlan.locator('[data-plan-picker-trigger]').click();
	const planDialog = page.getByRole('dialog', { name: 'Group plan' });
	await expect(planDialog).toBeVisible({ timeout: 15000 });
	await planDialog.getByRole('row').filter({ hasText: planName }).click();
	await planDialog.getByRole('button', { name: /^Select/ }).click();
	await expect(planDialog).toHaveCount(0);
	await expect(groupPlan.locator('[data-plan-picker-label]')).toHaveText(planName);
	await expect(page.locator('[data-plan-impact]').getByText(username)).toBeVisible({ timeout: 15000 });
	await screenshot(page, JOURNEY, 'group-plan-impact');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByText('1 unsaved change')).toHaveCount(0, { timeout: 15000 });

	await page.goto(`/admin?tab=users&id=${userId}`);
	const effective = page.locator('[data-effective-limit="generations_per_day"]');
	await expect(effective).toBeVisible({ timeout: 15000 });
	await expect(effective.locator('[data-limit-source]')).toContainText(groupName);
	await expect(effective).toContainText('5');
	await screenshot(page, JOURNEY, 'user-effective-limits');
});
