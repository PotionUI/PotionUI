/**
 * Core + plugin-registered mountable Svelte components, keyed by name (e.g.
 * `GenerationHistoryModal`). This is just the components map - the rest of
 * the `window.__potionui` host API surface (form reactions, field/renderer
 * registration, registries) is assembled by `plugin-api/host.ts`.
 */
import { unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';

export interface MountableComponent {
	mount: (target: HTMLElement, props: Record<string, any>) => any;
	/**
	 * Push new props into a mounted instance.
	 *
	 * Core form fields are CONTROLLED: they derive their display from `value`
	 * and report edits through `onChange`, expecting the parent to write the
	 * new value back. Without an update path a host-mounted field's `value`
	 * is frozen at its initial prop, so its own reactive blocks clear the
	 * selection the user just made and the field looks unresponsive. Mounting
	 * through `createClassComponent` (rather than bare `mount`) is what makes
	 * this possible.
	 */
	update: (instance: any, props: Record<string, any>) => void;
	unmount: (instance: any) => void;
}

export type MountableComponentRegistry = Record<string, MountableComponent>;

const registry: MountableComponentRegistry = {};

export function registerComponent(name: string, component: any) {
	registry[name] = {
		mount: (target, props) => createClassComponent({ component, target, props }),
		update: (instance, props) => instance?.$set?.(props),
		unmount: (instance) => {
			if (typeof instance?.$destroy === 'function') instance.$destroy();
			else unmount(instance);
		}
	};
}

export function getRegistry(): MountableComponentRegistry {
	return registry;
}
