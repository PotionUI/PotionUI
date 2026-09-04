<script lang="ts">
	// Top-level Video Director editor: modeless -- there is no mode switch.
	// The Shot Console (W1's rework of the old Stage+Rail composition) is the
	// entire surface. This file only owns the section wrapper and the
	// container query the console's 1440 layout keys off -- no header of its
	// own: console.html's `.dh-row` (rendered by ConsoleHeader.svelte, inside
	// ShotConsole) is THE header; a second "Video Director" title here was a
	// duplicate the console render spec caught. There is also no Variables
	// entry point here any more (the mock's `.dh-row` has no slot for one,
	// and the maintainer's header-contents ruling didn't add one) -- Video
	// Director now reaches the Variables modal the same way Prompt Relay
	// always has, via PromptSection.svelte's own standalone row above this
	// component (see that file's `promptRelayActive || videoDirectorActive`
	// condition).
	//
	// The document itself (the `lastEmitted` re-entrancy guard, `mode` kept
	// coherent via `deriveDirectorMode`) and all selection state live in
	// ShotConsole.svelte -- see that file's own header note.
	import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
	import ShotConsole from './console/ShotConsole.svelte';

	let { value, capabilities, presetId, formData, onChange }: {
		value: VideoDirectorValue | undefined;
		capabilities: DirectorCapabilities;
		presetId: string;
		/** The generate form's own field values -- threaded down so a Director
		 * media slot can offer "From form" (Stage B reference media). */
		formData: Record<string, unknown> | null | undefined;
		onChange: (v: VideoDirectorValue) => void;
	} = $props();
</script>

<section class="video-director" aria-label="Video Director">
	<ShotConsole {value} {capabilities} {presetId} {formData} {onChange} />
</section>

<style>
	.video-director {
		container-type: inline-size;
		container-name: video-director;
	}
</style>
