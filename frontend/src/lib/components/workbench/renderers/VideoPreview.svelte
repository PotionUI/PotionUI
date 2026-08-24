<script lang="ts">
	/**
	 * `workbench.file` core default for `file_type: "video"`. Thin wrapper
	 * around `VideoPlayer`, extracted from `Workbench.svelte`'s inline video
	 * branch. `Workbench` drives a custom controls bar via `bind:this` on the
	 * resolved renderer component, so every method/reactive prop `VideoPlayer`
	 * exposes is forwarded here unchanged.
	 */
	import VideoPlayer from '$lib/components/workbench/VideoPlayer.svelte';

	export let videoUrl: string;
	export let comparisonVideoUrl: string | null = null;
	export let isComparing: boolean = false;
	export let isMuted: boolean = true;

	let inner: VideoPlayer;

	export let isRegularVideoPlaying = false;
	export let regularVideoCurrentTime = 0;
	export let regularVideoDuration = 0;

	export function resetPlayState() {
		inner?.resetPlayState();
	}
	export function handleRegularVideoTogglePlayPause() {
		inner?.handleRegularVideoTogglePlayPause();
	}
	export function handleRegularVideoSeek(event: MouseEvent) {
		inner?.handleRegularVideoSeek(event);
	}
	export function handleVideoToggleMute() {
		inner?.handleVideoToggleMute();
	}
</script>

<VideoPlayer
	bind:this={inner}
	bind:isRegularVideoPlaying
	bind:regularVideoCurrentTime
	bind:regularVideoDuration
	{videoUrl}
	{comparisonVideoUrl}
	{isComparing}
	{isMuted}
	on:exitComparison
	on:muteChange
/>
