import { expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

export const OWNER = {
	username: process.env.E2E_USERNAME || 'e2e-owner',
	password: process.env.E2E_PASSWORD || ''
};

const ARTIFACTS_DIR = process.env.E2E_ARTIFACTS_DIR || join(process.cwd(), 'tests', 'e2e', '.playwright-artifacts');

/** Absolute path for a labeled screenshot under artifacts/<journey>/<label>.png. */
export function shotPath(journey: string, label: string): string {
	const dir = join(ARTIFACTS_DIR, journey);
	mkdirSync(dir, { recursive: true });
	return join(dir, `${label}.png`);
}

/** Save a full-page screenshot of the decisive state. Returns its path. */
export async function screenshot(page: Page, journey: string, label: string): Promise<string> {
	const path = shotPath(journey, label);
	await page.evaluate(() => document.fonts.ready);
	await page.screenshot({ path, fullPage: true, animations: 'disabled' });
	return path;
}

export const AUTH_STATE_PATH = join(ARTIFACTS_DIR, '.auth', 'owner.json');

export const SIGNED_OUT = { cookies: [], origins: [] };

export async function loginAsOwnerViaForm(page: Page): Promise<void> {
	await page.goto('/login');
	await page.locator('#username').fill(OWNER.username);
	await page.locator('#password').fill(OWNER.password);
	await page.getByRole('button', { name: 'Sign In' }).click();
	await page.waitForURL(/\/generate/, { timeout: 15000 });
}

export async function loginAsOwner(page: Page): Promise<void> {
	await page.goto('/generate');
	const seeded = await page.evaluate(() => !!localStorage.getItem('auth_token'));
	if (!seeded) {
		await loginAsOwnerViaForm(page);
		return;
	}
	await page.waitForURL(/\/generate/, { timeout: 15000 });
}

export async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

export async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

export async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

/** The instance owner's bearer token, read from localStorage after login. */
export async function ownerToken(page: Page): Promise<string> {
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	expect(token, 'expected auth_token in localStorage after login').toBeTruthy();
	return token as string;
}

export async function clearTabDrafts(page: Page): Promise<void> {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	const list = await page.request.get('/api/tab-drafts', { headers });
	expect(list.ok(), `GET /api/tab-drafts -> ${list.status()}`).toBeTruthy();
	const drafts = ((await list.json()).data ?? []) as Array<{ tab_id: string }>;
	for (const { tab_id } of drafts) {
		const removed = await page.request.delete(`/api/tab-drafts/${encodeURIComponent(tab_id)}`, { headers });
		expect(removed.ok(), `DELETE /api/tab-drafts/${tab_id} -> ${removed.status()}`).toBeTruthy();
	}
}

export async function scopedStorageKey(page: Page, base: string): Promise<string> {
	return page.evaluate((key) => {
		try {
			const registry = JSON.parse(localStorage.getItem('potionui_accounts') || 'null');
			const id = registry?.activeId || localStorage.getItem('auth_last_user_id');
			return id ? `${key}::${id}` : key;
		} catch {
			return key;
		}
	}, base);
}

export async function waitForFormPublished(page: Page): Promise<void> {
	await expect(page.locator('section[aria-label] > div.space-y-4').first()).toBeAttached({ timeout: 20000 });
	await expect(page.getByText('Loading Form...')).toHaveCount(0, { timeout: 20000 });
}

export async function clickDialogSaveAndAwaitResponse(page: Page): Promise<void> {
	const [response] = await Promise.all([
		page.waitForResponse(
			(res) => res.url().includes('/api/sessions/save') && res.request().method() === 'POST',
			{ timeout: 30000 }
		),
		page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click()
	]);
	const body = response.ok() ? '' : await response.text();
	expect(response.ok(), `POST /api/sessions/save -> ${response.status()} ${body}`).toBeTruthy();
}

export async function expectNoBoxOverlap(
	items: Array<{ name: string; locator: import('@playwright/test').Locator }>
): Promise<void> {
	const boxes: Array<{ name: string; x: number; y: number; width: number; height: number }> = [];
	for (const { name, locator } of items) {
		const count = await locator.count();
		for (let i = 0; i < count; i++) {
			const box = await locator.nth(i).boundingBox();
			if (box && box.width > 0 && box.height > 0) boxes.push({ name: `${name}[${i}]`, ...box });
		}
	}
	for (let a = 0; a < boxes.length; a++) {
		for (let b = a + 1; b < boxes.length; b++) {
			const p = boxes[a];
			const q = boxes[b];
			const overlap =
				p.x < q.x + q.width - 0.5 &&
				q.x < p.x + p.width - 0.5 &&
				p.y < q.y + q.height - 0.5 &&
				q.y < p.y + p.height - 0.5;
			expect(overlap, `${p.name} overlaps ${q.name}`).toBe(false);
		}
	}
}

export interface AdminSocketMock {
	pushIndexingStatus(status: Record<string, unknown>): Promise<void>;
}

export async function mockAdminSocket(
	page: Page,
	initialIndexingStatus?: () => Record<string, unknown>
): Promise<AdminSocketMock> {
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket('**/ws/admin**', (ws) => {
		socket = ws;
		ws.send(JSON.stringify({ type: 'connection_established' }));
		if (initialIndexingStatus) {
			ws.send(JSON.stringify({ type: 'indexing_status', status: initialIndexingStatus() }));
		}
	});
	return {
		async pushIndexingStatus(status) {
			await expect.poll(() => socket !== null, { timeout: 15000 }).toBe(true);
			(socket as WebSocketRoute | null)!.send(JSON.stringify({ type: 'indexing_status', status }));
		}
	};
}

export async function addGenerationTab(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menuitem', { name: /New generation/ }).click();
}
