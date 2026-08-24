<script lang="ts">
	/**
	 * `workbench.file` core default for `file_type: "audio"`. Thin wrapper
	 * around `AudioPlayer`, extracted from `Workbench.svelte`'s inline
	 * audio branch.
	 */
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import type { AudioTrack } from '$lib/types/audio';

	export let tracks: AudioTrack[] = [];
	export let showWaveform: boolean = true;
	export let showDownload: boolean = true;
	export let className: string = '';
</script>

<div class="w-full h-full flex items-center justify-center p-8">
	<div class="w-full max-w-2xl">
		<AudioPlayer {tracks} {showWaveform} {showDownload} {className} on:play on:pause on:ended on:error on:download />
	</div>
</div>
