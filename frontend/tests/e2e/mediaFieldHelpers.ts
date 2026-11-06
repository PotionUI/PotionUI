import { expect, type Locator, type Page } from '@playwright/test';

export type FieldToolId =
	| 'edit'
	| 'open-in-editor'
	| 'crop'
	| 'mask'
	| 'clear-mask'
	| 'trim'
	| 'frame'
	| 'split'
	| 'full'
	| 'earlier'
	| 'later'
	| 'replace'
	| 'remove'
	| 'remove-all';

export async function pickFieldTool(page: Page, field: Locator, id: FieldToolId) {
	await field.locator('[data-tools-trigger]').first().click();
	const item = page.locator(`[data-tools-menu] [data-tool="${id}"]`);
	await expect(item).toBeVisible();
	await item.click();
}

export function maskChip(field: Locator): Locator {
	return field.locator('[data-mask-chip]');
}

export async function pickFromSourceMenu(page: Page, scope: Locator, source: string) {
	await scope.getByRole('button', { name: 'More ways to add' }).click();
	const item = page.locator(`[data-source-menu] [data-source="${source}"]`);
	await expect(item).toBeVisible();
	await item.click();
}

export function crc32(buf: Buffer): number {
	let c = ~0;
	for (const b of buf) {
		c ^= b;
		for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
	}
	return ~c >>> 0;
}

export function chunk(type: string, data: Buffer): Buffer {
	const head = Buffer.alloc(4);
	head.writeUInt32BE(data.length);
	const body = Buffer.concat([Buffer.from(type), data]);
	const tail = Buffer.alloc(4);
	tail.writeUInt32BE(crc32(body));
	return Buffer.concat([head, body, tail]);
}
