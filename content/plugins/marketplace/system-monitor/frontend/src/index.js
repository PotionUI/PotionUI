import { mount, unmount } from 'svelte';
import SystemMonitorWidget from './SystemMonitorWidget.svelte';

export { SystemMonitorWidget as default };

export function mountPlugin(target, props) {
    return mount(SystemMonitorWidget, { target, props });
}

export function unmountPlugin(instance) {
    unmount(instance);
}
