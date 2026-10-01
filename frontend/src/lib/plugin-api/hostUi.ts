import { getRegistry, registerLazyComponent } from './componentRegistry';

const GLOBS: Record<string, () => Promise<any>>[] = [
	import.meta.glob('$lib/components/ui/*.svelte'),
	import.meta.glob('$lib/components/detail/*.svelte'),
	import.meta.glob('$lib/components/library/*.svelte'),
	import.meta.glob('$lib/components/Tooltip.svelte'),
	import.meta.glob('$lib/components/Icon.svelte'),
	import.meta.glob('$lib/components/DynamicForm.svelte'),
	import.meta.glob('$lib/components/modals/UploadLibraryModal.svelte')
];

function componentName(path: string): string {
	return path.slice(path.lastIndexOf('/') + 1).replace(/\.svelte$/, '');
}

export function registerHostUiComponents(): void {
	const registry = getRegistry();
	for (const glob of GLOBS) {
		for (const [path, load] of Object.entries(glob)) {
			const name = componentName(path);
			if (!registry[name]) registerLazyComponent(name, load as () => Promise<{ default: any }>);
		}
	}
}
