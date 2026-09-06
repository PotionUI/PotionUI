<script lang="ts">
	// Legacy passthrough layer (mirrors GenerationPanels.svelte's `export let onDirectorCheckedChange`).
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import Inner from './DirectorCheckedLoopInner.svelte';
	export let value: VideoDirectorValue | undefined;
	export let capabilities: DirectorCapabilities;
	export let formData: Record<string, unknown> | null = null;
	export let onChange: (v: VideoDirectorValue) => void;
	export let onDirectorCheckedChange: ((checked: Set<string>) => void) | undefined = undefined;
</script>

<Inner {value} {capabilities} {formData} {onChange} {onDirectorCheckedChange} />
