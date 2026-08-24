import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'admin-assignment-card';

// The shared AssignmentCard, mounted on the LLM Configuration detail pane:
// it must render, a group toggle must persist through a reload, and the
// unassigned badge on the list row must clear once something is assigned.
test('admin can assign a group to an LLM configuration from its detail pane', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const groupName = `e2e-assign-group-${Date.now()}`;
	const groupCreate = await page.request.post('/api/user-groups/', {
		headers: { Authorization: `Bearer ${token}` },
		data: { name: groupName, description: null }
	});
	expect(groupCreate.ok(), `group create -> ${groupCreate.status()}`).toBeTruthy();

	const configName = `e2e-assign-config-${Date.now()}`;
	const configCreate = await page.request.post('/api/llm/configurations', {
		headers: { Authorization: `Bearer ${token}` },
		data: {
			name: configName,
			type: 'openai',
			enabled: true,
			base_url: 'https://api.example.invalid',
			model: 'gpt-4',
			system_message: 'You are a helpful assistant.'
		}
	});
	expect(configCreate.ok(), `config create -> ${configCreate.status()}`).toBeTruthy();

	await page.goto('/admin?tab=llm');

	const configList = page.locator('[role="listbox"][aria-label="LLM configurations"]');
	await expect(configList).toBeVisible({ timeout: 15000 });

	const configOption = configList.getByRole('option').filter({ hasText: configName });
	await expect(configOption).toBeVisible({ timeout: 15000 });

	// A freshly created, unassigned configuration carries the warning badge.
	await expect(configOption.getByText('Unassigned', { exact: true })).toBeVisible();

	await configOption.click();

	// The card lives behind the detail pane's Access tab, mirroring the
	// presets detail layout.
	const accessTab = page.locator('nav[aria-label="LLM configuration details"]').getByRole('button', { name: /Access/ });
	await expect(accessTab).toBeVisible({ timeout: 15000 });
	await accessTab.click();

	const assignmentCard = page.locator('[data-testid="assignment-card"]');
	await expect(assignmentCard, 'AssignmentCard should mount on the Access tab').toBeVisible({ timeout: 15000 });

	await assignmentCard.locator('nav[aria-label="Access type"]').getByRole('button', { name: /Groups/ }).click();

	const groupRow = assignmentCard.locator('[data-testid="assignment-row"]').filter({ hasText: groupName });
	await expect(groupRow).toBeVisible({ timeout: 15000 });
	await expect(groupRow.getByRole('button', { name: 'Add group' })).toBeVisible();

	await screenshot(page, JOURNEY, 'before-assign');
	await groupRow.getByRole('button', { name: 'Add group' }).click();
	await expect(groupRow.getByRole('button', { name: 'Remove' })).toBeVisible({ timeout: 10000 });

	// The unassigned badge on the list row must clear once something is assigned.
	await expect(configOption.getByText('Unassigned', { exact: true })).toHaveCount(0);

	await screenshot(page, JOURNEY, 'after-assign');

	// Reload: the toggle must have actually persisted server-side, not just in local state.
	await page.goto('/admin?tab=llm');
	await expect(configList).toBeVisible({ timeout: 15000 });
	await configList.getByRole('option').filter({ hasText: configName }).click();
	await page.locator('nav[aria-label="LLM configuration details"]').getByRole('button', { name: /Access/ }).click();
	await expect(assignmentCard).toBeVisible({ timeout: 15000 });
	await assignmentCard.locator('nav[aria-label="Access type"]').getByRole('button', { name: /Groups/ }).click();

	const groupRowAfterReload = assignmentCard.locator('[data-testid="assignment-row"]').filter({ hasText: groupName });
	await expect(groupRowAfterReload).toBeVisible({ timeout: 15000 });
	await expect(groupRowAfterReload.getByRole('button', { name: 'Remove' }), 'group assignment should survive a reload').toBeVisible({
		timeout: 10000
	});

	await screenshot(page, JOURNEY, 'after-reload');
});
