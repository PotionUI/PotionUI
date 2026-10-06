import { registerMediaTool, selectionCount, type MediaToolContext } from '$lib/tools/tools';
import { copyText } from '$lib/utils/clipboard';
import { toasts } from '$lib/stores/toast';
import { logger, getErrorMessage } from '$lib/utils/logger';

let registered = false;

function promptOf(ctx: MediaToolContext): string {
	const prompt = ctx.generations[0]?.form_data?.prompt;
	return typeof prompt === 'string' ? prompt.trim() : '';
}

function isStack(ctx: MediaToolContext): boolean {
	return !!ctx.entry?.stackCells;
}

async function toPngBlob(blob: Blob): Promise<Blob> {
	if (blob.type === 'image/png') return blob;
	const bitmap = await createImageBitmap(blob);
	const canvas = document.createElement('canvas');
	canvas.width = bitmap.width;
	canvas.height = bitmap.height;
	canvas.getContext('2d')?.drawImage(bitmap, 0, 0);
	return await new Promise<Blob>((resolve, reject) =>
		canvas.toBlob((out) => (out ? resolve(out) : reject(new Error('Could not encode the image'))), 'image/png')
	);
}

export async function copyImageToClipboard(url: string): Promise<void> {
	const response = await fetch(url);
	if (!response.ok) throw new Error('Could not load the image');
	const png = await toPngBlob(await response.blob());
	await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
}

export function downloadUrl(url: string, filename: string): void {
	const link = document.createElement('a');
	link.href = url;
	link.download = filename;
	link.click();
}

export function registerEntryTools(): void {
	if (registered) return;
	registered = true;

	const allStates = ['completed', 'failed', 'running'] as const;

	registerMediaTool({
		id: 'open-details',
		label: 'Open details',
		icon: 'eyes',
		shortcut: 'Enter',
		category: 'open',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['entry'],
		entryStates: [...allStates],
		requires: 'open',
		labelFor: (ctx) => {
			if (ctx.entry?.status === 'failed') return 'View error';
			if (isStack(ctx)) return 'Open grid';
			if (ctx.kinds.has('mesh')) return 'Open in 3D viewer';
			return 'Open details';
		},
		applies: () => ({ enabled: true }),
		run: (ctx, host) => host.open?.(ctx)
	});

	registerMediaTool({
		id: 'show-in-grid',
		label: 'Show in grid',
		icon: 'grid',
		category: 'open',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		requires: 'openGrid',
		applies: (ctx) => ({ enabled: true, hidden: !ctx.entry?.looseCell }),
		run: (ctx, host) => host.openGrid?.(ctx)
	});

	registerMediaTool({
		id: 'reuse-settings',
		label: 'Reuse settings',
		icon: 'refresh',
		category: 'open',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		entryStates: [...allStates],
		requires: 'reuse',
		applies: (ctx) => ({ enabled: true, hidden: !ctx.generations[0]?.preset_id }),
		run: (ctx, host) => host.reuse?.(ctx)
	});

	registerMediaTool({
		id: 'download',
		label: 'Download',
		icon: 'download',
		shortcut: 'D',
		category: 'export',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['entry'],
		applies: (ctx) => ({ enabled: true, hidden: isStack(ctx) || ctx.items.length === 0 }),
		run: (ctx) => {
			const item = ctx.items[0];
			if (item) downloadUrl(item.url, item.filename);
		}
	});

	registerMediaTool({
		id: 'copy-image',
		label: 'Copy image',
		icon: 'copy',
		category: 'export',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['entry'],
		kinds: ['image'],
		applies: (ctx) => ({
			enabled: true,
			hidden: isStack(ctx) || typeof ClipboardItem === 'undefined'
		}),
		run: async (ctx) => {
			const item = ctx.items[0];
			if (!item) return;
			try {
				await copyImageToClipboard(item.url);
				toasts.success('Image copied');
			} catch (error) {
				logger.error('Failed to copy the image:', getErrorMessage(error));
				toasts.error('Could not copy the image');
			}
		}
	});

	registerMediaTool({
		id: 'copy-prompt',
		label: 'Copy prompt',
		icon: 'document',
		category: 'export',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		applies: (ctx) => ({ enabled: true, hidden: !promptOf(ctx) }),
		run: async (ctx) => {
			const ok = await copyText(promptOf(ctx));
			if (ok) toasts.success('Prompt copied');
			else toasts.error('Could not copy the prompt');
		}
	});

	registerMediaTool({
		id: 'copy-error',
		label: 'Copy error',
		icon: 'copy',
		category: 'export',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		entryStates: ['failed'],
		applies: (ctx) => ({ enabled: true, hidden: !ctx.entry?.error }),
		run: async (ctx) => {
			const ok = await copyText(ctx.entry?.error ?? '');
			if (ok) toasts.success('Error copied');
			else toasts.error('Could not copy the error');
		}
	});

	registerMediaTool({
		id: 'pending-tools',
		label: 'Tools',
		icon: 'wand',
		category: 'compose',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		entryStates: ['running'],
		applies: () => ({ enabled: false, reason: 'when finished' })
	});

	registerMediaTool({
		id: 'rating',
		label: 'Rating',
		icon: 'star',
		category: 'organize',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		control: 'rating',
		requires: 'rate',
		applies: (ctx) => ({ enabled: true, hidden: isStack(ctx) }),
		run: (ctx, host, extra) => host.rate?.(ctx, extra?.rating ?? 0)
	});

	registerMediaTool({
		id: 'favorite',
		label: 'Favorite',
		icon: 'star',
		shortcut: 'F',
		category: 'organize',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry'],
		requires: 'favorite',
		labelFor: (ctx) => (ctx.generations[0]?.is_favorite ? 'Remove favorite' : 'Favorite'),
		applies: (ctx) => ({ enabled: true, hidden: isStack(ctx) }),
		run: (ctx, host) => host.favorite?.(ctx)
	});

	registerMediaTool({
		id: 'add-to-collection',
		label: 'Add to collection',
		icon: 'folder-plus',
		category: 'organize',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['entry', 'selection'],
		control: 'collection',
		requires: 'addToCollection',
		applies: () => ({ enabled: true }),
		run: (ctx, host, extra) => {
			if (extra?.collectionId) host.addToCollection?.(ctx, extra.collectionId);
		}
	});

	registerMediaTool({
		id: 'copy-to-library',
		label: 'Copy to Library',
		icon: 'photo',
		category: 'organize',
		source: 'core',
		scopes: ['history'],
		surfaces: ['entry', 'selection'],
		requires: 'copyToLibrary',
		applies: (ctx) => ({ enabled: true, hidden: isStack(ctx) || ctx.items.length === 0 }),
		run: (ctx, host) => host.copyToLibrary?.(ctx)
	});

	registerMediaTool({
		id: 'deselect',
		label: 'Deselect this card',
		icon: 'close',
		category: 'danger',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['selection'],
		requires: 'deselect',
		applies: () => ({ enabled: true }),
		run: (ctx, host) => host.deselect?.(ctx)
	});

	registerMediaTool({
		id: 'delete',
		label: 'Delete',
		icon: 'trash',
		shortcut: 'Backspace',
		category: 'danger',
		source: 'core',
		scopes: ['history', 'library'],
		surfaces: ['entry', 'selection'],
		entryStates: [...allStates],
		tone: 'danger',
		requires: 'remove',
		labelFor: (ctx) => {
			if (ctx.entry?.status === 'running') return 'Cancel generation';
			if (ctx.entry?.stackCells) return `Delete grid (${ctx.entry.stackCells} cells)`;
			const count = selectionCount(ctx);
			return count > 1 ? `Delete ${count}` : 'Delete';
		},
		applies: () => ({ enabled: true }),
		run: (ctx, host) => {
			if (ctx.entry?.status === 'running' && host.cancel) host.cancel(ctx);
			else host.remove?.(ctx);
		}
	});
}
