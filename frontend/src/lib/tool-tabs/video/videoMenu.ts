import { CORE_OWNER } from '$lib/registries/registry';
import { menuItems, type EditorMenuContext } from '$lib/editors/menuItems';

const frameReason = (ctx: EditorMenuContext): true | string => {
	if (!ctx.hasSource) return 'Open a clip first';
	return ctx.busy ? 'Wait until the current save finishes' : true;
};

let registered = false;

export function ensureVideoMenu(): void {
	if (registered) return;
	registered = true;
	menuItems.register(
		{
			id: 'file.frame-save',
			editor: 'video',
			menu: 'file',
			group: 'out',
			order: 40,
			label: 'Save frame as image',
			icon: 'image',
			available: frameReason,
			run: (ctx) => ctx.commands.frame?.save()
		},
		CORE_OWNER
	);
	menuItems.register(
		{
			id: 'file.frame-open',
			editor: 'video',
			menu: 'file',
			group: 'out',
			order: 50,
			label: 'Open frame in Image editor',
			icon: 'sliders-horizontal',
			available: frameReason,
			run: (ctx) => ctx.commands.frame?.open()
		},
		CORE_OWNER
	);
}
