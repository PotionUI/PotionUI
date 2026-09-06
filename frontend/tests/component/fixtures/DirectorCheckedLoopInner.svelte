<script lang="ts">
	// Legacy passthrough layer (mirrors PromptSection.svelte's `export let onDirectorCheckedChange`
	// feeding VideoDirectorEditor's runes-mode `onCheckedChange` prop).
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import VideoDirectorEditor from '$lib/components/video-director/VideoDirectorEditor.svelte';
	export let value: VideoDirectorValue | undefined;
	export let capabilities: DirectorCapabilities;
	export let formData: Record<string, unknown> | null = null;
	export let onChange: (v: VideoDirectorValue) => void;
	export let onDirectorCheckedChange: ((checked: Set<string>) => void) | undefined = undefined;
</script>

<VideoDirectorEditor {value} {capabilities} presetId="test-preset" {formData} {onChange} onCheckedChange={onDirectorCheckedChange} />
