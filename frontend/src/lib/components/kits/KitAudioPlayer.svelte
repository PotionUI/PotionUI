<script lang="ts">
	import Waveform from '$lib/components/Waveform.svelte';
	import { IconButton } from '$lib/components/ui';
	import { readWaveformPalette, waveformConfig } from '$lib/components/form-fields/mediaFieldWaveform';
	import { formatTimecode } from '$lib/media/editors/timecode';

	let { url, name }: { url: string; name: string } = $props();

	let audio = $state<HTMLAudioElement | null>(null);
	let paused = $state(true);
	let current = $state(0);
	let length = $state(0);

	const config = waveformConfig(readWaveformPalette(), 48);

	function toggle() {
		if (!audio) return;
		if (audio.paused) void audio.play().catch(() => (paused = true));
		else audio.pause();
	}
</script>

<div class="flex h-full flex-col justify-between gap-2" data-kit-audio>
	<div class="h-12 text-fg-muted">
		<Waveform
			audioElement={audio}
			{url}
			{config}
			onSeek={(time) => {
				if (audio) audio.currentTime = time;
			}}
		/>
	</div>
	<div class="flex items-center justify-between gap-2">
		<IconButton icon={paused ? 'play' : 'pause'} label={paused ? `Play ${name}` : `Pause ${name}`} size="sm" variant="secondary" onclick={toggle} />
		<span class="font-mono text-sm tabular-nums text-fg-subtle">{formatTimecode(current)} / {formatTimecode(length)}</span>
	</div>
	<audio
		bind:this={audio}
		src={url}
		preload="metadata"
		class="hidden"
		bind:paused
		bind:currentTime={current}
		bind:duration={length}
	></audio>
</div>
