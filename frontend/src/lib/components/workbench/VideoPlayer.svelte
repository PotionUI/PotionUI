<script lang="ts">
	import { createEventDispatcher, onDestroy, tick } from 'svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	// Props
	export let videoUrl: string;
	export let comparisonVideoUrl: string | null = null;
	export let isComparing: boolean = false;
	export let isMuted: boolean = true;

	// Exported state for parent to render controls
	export let isRegularVideoPlaying = false;
	export let regularVideoCurrentTime = 0;
	export let regularVideoDuration = 0;
	export let isVideoPlaying = false;
	export let videoCurrentTime = 0;
	export let videoDuration = 0;

	const dispatch = createEventDispatcher<{
		exitComparison: void;
		muteChange: boolean;
	}>();

	// Compare mode
	let compareMode: 'slider' | 'side-by-side' = 'slider';

	// Internal DOM refs
	let primaryVideoEl: HTMLVideoElement | null = null;
	let comparisonVideoEl: HTMLVideoElement | null = null;
	let regularVideoEl: HTMLVideoElement | null = null;
	let comparisonContainerEl: HTMLDivElement | null = null;
	let primaryVideoDuration = 0;
	let comparisonVideoDuration = 0;

	// Internal sync state
	let isSyncingVideo = false;
	let videoSyncAnimFrame: number | null = null;
	let regularVideoAnimFrame: number | null = null;

	// Comparison slider state
	let sliderPosition = 50;
	let isDraggingSlider = false;
	let activeRegularVideoUrl = videoUrl;

	$: if (!isComparing && videoUrl !== activeRegularVideoUrl) {
		activeRegularVideoUrl = videoUrl;
		stopRegularVideoSync();
		isRegularVideoPlaying = false;
		regularVideoCurrentTime = 0;
		regularVideoDuration = 0;
	}

	// Reset state when comparison mode is toggled off
	$: if (!isComparing) {
		stopVideoSync();
		isVideoPlaying = false;
		videoCurrentTime = 0;
		videoDuration = 0;
		compareMode = 'slider';
		sliderPosition = 50;
		isDraggingSlider = false;
	}

	onDestroy(() => {
		stopVideoSync();
		stopRegularVideoSync();
	});

	// ---- Public API for parent to call ----
	export function resetPlayState() {
		isRegularVideoPlaying = false;
		stopRegularVideoSync();
	}

	export function handleRegularVideoTogglePlayPause() {
		if (!regularVideoEl) return;
		if (!regularVideoEl.paused) {
			regularVideoEl.pause();
		} else {
			void regularVideoEl.play().catch(() => {
				isRegularVideoPlaying = false;
			});
		}
	}

	export function handleRegularVideoSeek(event: MouseEvent) {
		const bar = event.currentTarget as HTMLElement;
		const rect = bar.getBoundingClientRect();
		const x = event.clientX - rect.left;
		const pct = Math.max(0, Math.min(1, x / rect.width));
		const newTime = pct * regularVideoDuration;
		if (regularVideoEl) regularVideoEl.currentTime = newTime;
		regularVideoCurrentTime = newTime;
	}

	export function handleVideoToggleMute() {
		const newMuted = !isMuted;
		if (regularVideoEl) regularVideoEl.muted = newMuted;
		if (primaryVideoEl) primaryVideoEl.muted = newMuted;
		if (comparisonVideoEl) comparisonVideoEl.muted = newMuted;
		dispatch('muteChange', newMuted);
	}

	export function handleVideoTogglePlayPause() {
		if (isVideoPlaying) {
			handleVideoPause();
		} else {
			handleVideoPlay();
		}
	}

	export function handleVideoSeek(event: MouseEvent) {
		const bar = event.currentTarget as HTMLElement;
		const rect = bar.getBoundingClientRect();
		const x = event.clientX - rect.left;
		const pct = Math.max(0, Math.min(1, x / rect.width));
		const newTime = pct * videoDuration;
		if (primaryVideoEl) primaryVideoEl.currentTime = newTime;
		if (comparisonVideoEl) comparisonVideoEl.currentTime = newTime;
		videoCurrentTime = newTime;
	}

	export async function handleToggleCompareMode() {
		compareMode = compareMode === 'slider' ? 'side-by-side' : 'slider';
		await tick();
		syncVideoPositions();
	}

	export function getCompareMode() {
		return compareMode;
	}

	// ---- Internal helpers ----
	function formatTime(seconds: number): string {
		if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	function startVideoSync() {
		stopVideoSync();
		let syncFrameCount = 0;
		function loop() {
			syncFrameCount++;
			if (syncFrameCount % 2 === 0) {
				if (!primaryVideoEl || !comparisonVideoEl) {
					videoSyncAnimFrame = requestAnimationFrame(loop);
					return;
				}
				if (!isSyncingVideo) {
					if (videoDuration > 0 && primaryVideoEl.currentTime >= videoDuration) {
						primaryVideoEl.currentTime = 0;
						comparisonVideoEl.currentTime = 0;
					}
					videoCurrentTime = primaryVideoEl.currentTime;
					const drift = Math.abs(primaryVideoEl.currentTime - comparisonVideoEl.currentTime);
					if (drift > 0.05) {
						isSyncingVideo = true;
						comparisonVideoEl.currentTime = primaryVideoEl.currentTime;
						isSyncingVideo = false;
					}
				}
			}
			videoSyncAnimFrame = requestAnimationFrame(loop);
		}
		videoSyncAnimFrame = requestAnimationFrame(loop);
	}

	function stopVideoSync() {
		if (videoSyncAnimFrame !== null) {
			cancelAnimationFrame(videoSyncAnimFrame);
			videoSyncAnimFrame = null;
		}
	}

	function syncVideoPositions() {
		if (primaryVideoEl && comparisonVideoEl) {
			comparisonVideoEl.currentTime = primaryVideoEl.currentTime;
		}
	}

	function handleVideoPlay() {
		if (!primaryVideoEl || !comparisonVideoEl) return;
		void Promise.all([primaryVideoEl.play(), comparisonVideoEl.play()])
			.then(() => {
				isVideoPlaying = true;
				startVideoSync();
			})
			.catch(() => {
				isVideoPlaying = false;
			});
	}

	function handleVideoPause() {
		if (!primaryVideoEl || !comparisonVideoEl) return;
		primaryVideoEl.pause();
		comparisonVideoEl.pause();
		isVideoPlaying = false;
		stopVideoSync();
	}

	function finiteDuration(video: HTMLVideoElement | null): number {
		return video && Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
	}

	function updateComparisonDuration() {
		const availableDurations = [primaryVideoDuration, comparisonVideoDuration].filter(
			(duration) => duration > 0
		);
		videoDuration = availableDurations.length > 0 ? Math.min(...availableDurations) : 0;
		videoCurrentTime = Math.min(primaryVideoEl?.currentTime ?? 0, videoDuration || Infinity);
	}

	function handlePrimaryVideoLoaded() {
		primaryVideoDuration = finiteDuration(primaryVideoEl);
		updateComparisonDuration();
	}

	function handleComparisonVideoLoaded() {
		comparisonVideoDuration = finiteDuration(comparisonVideoEl);
		updateComparisonDuration();
	}

	function startRegularVideoSync() {
		stopRegularVideoSync();
		let regularFrameCount = 0;
		function loop() {
			regularFrameCount++;
			if (regularFrameCount % 2 === 0) {
				if (!regularVideoEl) {
					regularVideoAnimFrame = requestAnimationFrame(loop);
					return;
				}
				regularVideoCurrentTime = regularVideoEl.currentTime;
			}
			regularVideoAnimFrame = requestAnimationFrame(loop);
		}
		regularVideoAnimFrame = requestAnimationFrame(loop);
	}

	function stopRegularVideoSync() {
		if (regularVideoAnimFrame !== null) {
			cancelAnimationFrame(regularVideoAnimFrame);
			regularVideoAnimFrame = null;
		}
	}

	function handleRegularVideoLoaded() {
		if (regularVideoEl) {
			regularVideoDuration = finiteDuration(regularVideoEl);
			regularVideoCurrentTime = regularVideoEl.currentTime;
		}
	}

	function handleRegularVideoTimeUpdate() {
		if (!regularVideoEl) return;
		regularVideoCurrentTime = regularVideoEl.currentTime;
		if (!regularVideoDuration) handleRegularVideoLoaded();
	}

	function handleRegularVideoEnded() {
		if (regularVideoEl) {
			regularVideoEl.currentTime = 0;
			regularVideoEl.play();
		}
	}

	// ---- Comparison slider ----
	function updateSliderPosition(clientX: number) {
		if (!comparisonContainerEl) return;
		const rect = comparisonContainerEl.getBoundingClientRect();
		if (rect.width <= 0) return;
		sliderPosition = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
	}

	function handleSliderPointerDown(event: PointerEvent) {
		if (event.button !== 0 && event.pointerType === 'mouse') return;
		event.preventDefault();
		isDraggingSlider = true;
		comparisonContainerEl?.setPointerCapture(event.pointerId);
		updateSliderPosition(event.clientX);
	}

	function handleSliderPointerMove(event: PointerEvent) {
		if (!isDraggingSlider) return;
		updateSliderPosition(event.clientX);
	}

	function handleSliderPointerUp(event: PointerEvent) {
		if (!isDraggingSlider) return;
		updateSliderPosition(event.clientX);
		isDraggingSlider = false;
		if (comparisonContainerEl?.hasPointerCapture(event.pointerId)) {
			comparisonContainerEl.releasePointerCapture(event.pointerId);
		}
	}

	function handleSliderKeydown(event: KeyboardEvent) {
		const step = event.shiftKey ? 10 : 2;
		let nextPosition = sliderPosition;
		if (event.key === 'ArrowLeft') nextPosition -= step;
		else if (event.key === 'ArrowRight') nextPosition += step;
		else if (event.key === 'Home') nextPosition = 0;
		else if (event.key === 'End') nextPosition = 100;
		else return;
		event.preventDefault();
		sliderPosition = Math.max(0, Math.min(100, nextPosition));
	}
</script>

{#if isComparing && comparisonVideoUrl}
	<!-- Video Comparison overlay -->
	<div class="absolute inset-0 z-30 flex flex-col bg-surface-1">
		<!-- Video area -->
		<div class="flex-1 relative min-h-0">
			{#if compareMode === 'slider'}
				<div
					bind:this={comparisonContainerEl}
					class="absolute inset-0 comparison-container cursor-col-resize touch-none select-none"
					on:pointerdown={handleSliderPointerDown}
					on:pointermove={handleSliderPointerMove}
					on:pointerup={handleSliderPointerUp}
					on:pointercancel={handleSliderPointerUp}
					role="group"
					aria-label="Video comparison slider"
				>
					<div class="absolute inset-0">
						<video
							bind:this={primaryVideoEl}
							src={videoUrl}
							class="w-full h-full object-contain"
							muted={isMuted}
							loop
							playsinline
							on:loadedmetadata={handlePrimaryVideoLoaded}
							on:durationchange={handlePrimaryVideoLoaded}
							on:timeupdate={() => (videoCurrentTime = primaryVideoEl?.currentTime ?? 0)}
						></video>
						<div class="absolute top-4 right-4 bg-black/70 text-white text-xs px-2 py-1 rounded backdrop-blur-sm pointer-events-none">
							Current
						</div>
					</div>

					<div
						class="absolute inset-0"
						style="clip-path: inset(0 {100 - sliderPosition}% 0 0);"
					>
						<video
							bind:this={comparisonVideoEl}
							src={comparisonVideoUrl}
							class="w-full h-full object-contain"
							muted={isMuted}
							loop
							playsinline
							on:loadedmetadata={handleComparisonVideoLoaded}
							on:durationchange={handleComparisonVideoLoaded}
						></video>
						<div class="absolute top-4 left-4 bg-black/70 text-white text-xs px-2 py-1 rounded backdrop-blur-sm pointer-events-none">
							Comparison
						</div>
					</div>

					<!-- Slider line and handle -->
					<div
						class="absolute top-0 bottom-0 w-0.5 bg-white/90 shadow-[0_0_0_1px_rgba(0,0,0,0.35),0_0_16px_rgba(0,0,0,0.35)] pointer-events-none"
						style="left: clamp(0px, {sliderPosition}%, 100%);"
					>
						<div
							class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-9 h-9 bg-surface-1/95 text-fg rounded-full shadow-xl ring-2 ring-white/90 flex items-center justify-center pointer-events-auto cursor-col-resize focus:outline-none focus:ring-signal"
							on:pointerdown={handleSliderPointerDown}
							on:keydown={handleSliderKeydown}
							role="slider"
							tabindex="0"
							aria-label="Video comparison slider"
							aria-valuenow={sliderPosition}
							aria-valuemin={0}
							aria-valuemax={100}
							aria-valuetext={`${Math.round(sliderPosition)}% comparison visible`}
						>
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7l-5 5 5 5M16 7l5 5-5 5" />
							</svg>
						</div>
					</div>
				</div>
			{:else}
				<div class="flex h-full">
					<div class="flex-1 relative min-w-0">
						<video
							bind:this={primaryVideoEl}
							src={videoUrl}
							class="w-full h-full object-contain"
							muted={isMuted}
							loop
							playsinline
							on:loadedmetadata={handlePrimaryVideoLoaded}
							on:durationchange={handlePrimaryVideoLoaded}
							on:timeupdate={() => (videoCurrentTime = primaryVideoEl?.currentTime ?? 0)}
						></video>
						<div class="absolute top-4 left-4 bg-black/70 text-white text-xs px-2 py-1 rounded backdrop-blur-sm pointer-events-none">
							Current
						</div>
					</div>
					<div class="w-px bg-line-hover flex-shrink-0"></div>
					<div class="flex-1 relative min-w-0">
						<video
							bind:this={comparisonVideoEl}
							src={comparisonVideoUrl}
							class="w-full h-full object-contain"
							muted={isMuted}
							loop
							playsinline
							on:loadedmetadata={handleComparisonVideoLoaded}
							on:durationchange={handleComparisonVideoLoaded}
						></video>
						<div class="absolute top-4 left-4 bg-black/70 text-white text-xs px-2 py-1 rounded backdrop-blur-sm pointer-events-none">
							Comparison
						</div>
					</div>
				</div>
			{/if}
		</div>

		<!-- Comparison controls bar (inside the overlay, below video) -->
		<div class="flex-shrink-0 bg-canvas border-t border-line-strong px-4 py-2 flex items-center gap-3">
			<Tooltip text={isVideoPlaying ? 'Pause both videos' : 'Play both videos'} position="top">
			<button on:click={handleVideoTogglePlayPause} class="text-fg hover:text-signal transition-colors p-1" aria-label={isVideoPlaying ? 'Pause both videos' : 'Play both videos'}>
				{#if isVideoPlaying}
					<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
						<path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
					</svg>
				{:else}
					<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
						<path d="M8 5v14l11-7z" />
					</svg>
				{/if}
			</button>
			</Tooltip>

			<span class="text-xs text-fg-muted font-mono tabular-nums w-20 text-center flex-shrink-0">
				{formatTime(videoCurrentTime)} / {formatTime(videoDuration || 0)}
			</span>

			<div
				class="flex-1 h-2 bg-surface-3 rounded-full cursor-pointer relative group/seek"
				on:click={handleVideoSeek}
				role="slider"
				aria-label="Seek video"
				aria-valuenow={videoCurrentTime}
				aria-valuemin={0}
				aria-valuemax={videoDuration}
				tabindex="0"
			>
				<div
					class="h-full bg-signal rounded-full transition-[width] duration-75"
					style="width: {videoDuration > 0 ? (videoCurrentTime / videoDuration) * 100 : 0}%;"
				></div>
				<div
					class="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-fg rounded-full shadow opacity-0 group-hover/seek:opacity-100 transition-opacity"
					style="left: {videoDuration > 0 ? (videoCurrentTime / videoDuration) * 100 : 0}%;"
				></div>
			</div>

			<Tooltip text={isMuted ? 'Unmute videos' : 'Mute videos'} position="top">
			<button on:click={handleVideoToggleMute} class="text-fg hover:text-signal transition-colors p-1 flex-shrink-0" aria-label={isMuted ? 'Unmute videos' : 'Mute videos'}>
				{#if isMuted}
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
							d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
					</svg>
				{:else}
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
							d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
					</svg>
				{/if}
			</button>
			</Tooltip>

			<Tooltip text={compareMode === 'slider' ? 'Show side by side' : 'Use comparison slider'} position="top">
			<button on:click={handleToggleCompareMode} class="text-fg hover:text-signal transition-colors p-1" aria-label={compareMode === 'slider' ? 'Show side by side' : 'Use comparison slider'}>
				{#if compareMode === 'slider'}
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 4H5a1 1 0 00-1 1v14a1 1 0 001 1h4m6-16h4a1 1 0 011 1v14a1 1 0 01-1 1h-4m-3-16v16" />
					</svg>
				{:else}
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m-8-8h16" />
					</svg>
				{/if}
			</button>
			</Tooltip>

			<Tooltip text="Exit comparison" position="top">
			<button on:click={() => dispatch('exitComparison')} class="text-fg hover:text-danger transition-colors p-1" aria-label="Exit comparison">
				<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
			</Tooltip>
		</div>
	</div>
{:else}
	<!-- Regular (non-comparison) video -->
	<video
		bind:this={regularVideoEl}
		src={videoUrl}
		class="w-full h-full object-contain"
		muted={isMuted}
		playsinline
		on:loadedmetadata={handleRegularVideoLoaded}
		on:durationchange={handleRegularVideoLoaded}
		on:timeupdate={handleRegularVideoTimeUpdate}
		on:ended={handleRegularVideoEnded}
		autoplay
		on:play={() => { isRegularVideoPlaying = true; startRegularVideoSync(); }}
		on:pause={() => { isRegularVideoPlaying = false; stopRegularVideoSync(); }}
	></video>
{/if}
