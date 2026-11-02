import { registerToolKind } from './registry';

let registered = false;

export function registerBuiltinToolKinds(): void {
	if (registered) return;
	registered = true;
	registerToolKind({
		tool: 'image',
		label: 'Image editor',
		description: 'Crop, resize, mask, effects',
		icon: 'sliders-horizontal',
		accepts: ['image'],
		order: 10,
		load: () => import('./placeholders/ImageToolPlaceholder.svelte')
	});
	registerToolKind({
		tool: 'video',
		label: 'Video editor',
		description: 'Trim, speed, audio, effects on one clip',
		icon: 'video',
		accepts: ['video'],
		order: 20,
		load: () => import('./placeholders/VideoToolPlaceholder.svelte')
	});
}
