import { CORE_OWNER } from '$lib/registries/registry';
import { isPaintEntry } from '$lib/paint/entries';
import { activeEditorApi, paintActions } from '$lib/paint/registries';
import type { PaintAction } from '$lib/paint/types';
import { guard, reportFailure } from './guard';
import { menuItems } from './menuItems';

const FIRST_ORDER = 5000;
const held = new Map<string, { action: PaintAction; dispose: () => void }>();
let registered = false;

function run(action: PaintAction): void {
	const editor = activeEditorApi();
	if (!editor) return;
	const entry = `paint:action:${action.id}`;
	guard(
		entry,
		'run',
		() => {
			const pending = action.run(editor);
			if (pending && typeof pending.catch === 'function') pending.catch((error: unknown) => reportFailure(entry, 'run', error));
		},
		undefined
	);
}

function sync(): void {
	const wanted = new Map(paintActions.list().filter((action) => typeof action.label === 'string' && action.label).map((action) => [action.id, action]));
	for (const [id, entry] of held) {
		if (wanted.get(id) === entry.action) continue;
		entry.dispose();
		held.delete(id);
	}
	let order = FIRST_ORDER;
	for (const [id, action] of wanted) {
		order += 10;
		if (held.has(id)) continue;
		const dispose = menuItems.register(
			{
				id: `paint.action.${id}`,
				editor: 'image',
				menu: 'edit',
				group: 'paint-actions',
				order,
				label: action.label,
				icon: 'wand',
				visible: (ctx) => isPaintEntry(ctx.tool),
				available: () => (activeEditorApi() ? true : 'Paint is not open'),
				run: () => run(action)
			},
			CORE_OWNER
		);
		held.set(id, { action, dispose });
	}
}

export function ensurePaintMenu(): void {
	if (registered) return;
	registered = true;
	sync();
	paintActions.subscribe(sync);
}
