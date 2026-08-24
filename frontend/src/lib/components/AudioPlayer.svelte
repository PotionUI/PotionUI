<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy, createEventDispatcher } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import type { AudioTrack, AudioTrackType, AudioPlayerState } from '$lib/types/audio';
	import Waveform from './Waveform.svelte';
	import { Alert } from '$lib/components/ui';

	const dispatch = createEventDispatcher();

	// Props
	export let tracks: AudioTrack[] = [];
	export let initialTrack: AudioTrackType = 'mixed';
	export let showWaveform: boolean = true;
	export let showDownload: boolean = true;
	export let autoplay: boolean = false;
	export let className: string = '';

	// Audio element
	let audioElement: HTMLAudioElement | null = null;

	// Player state
	let playerState: AudioPlayerState = {
		isPlaying: false,
		currentTime: 0,
		duration: 0,
		volume: 1.0,
		selectedTrack: initialTrack,
		isLoading: false,
		error: undefined
	};

	// UI state
	let isDraggingTime = false;
	let isDraggingVolume = false;
	let showVolumeSlider = false;
	let volumeTimeout: ReturnType<typeof setTimeout> | null = null;

	// The URL currently loaded into the <audio> element. Tracked explicitly
	// so a track only (re)loads on a genuine URL change, not on every
	// re-render that happens to re-touch `currentUrl`/`audioElement`.
	let loadedUrl: string | null = null;

	// Reactive computed values
	$: currentTrack = tracks.find((t) => t.type === playerState.selectedTrack);
	$: currentUrl = currentTrack?.url || currentTrack?.originalUrl || '';
	$: hasMultipleTracks = tracks.length > 1;
	$: formattedCurrentTime = formatTime(playerState.currentTime);
	$: formattedDuration = formatTime(playerState.duration);
	$: progressPercentage = playerState.duration > 0 ? (playerState.currentTime / playerState.duration) * 100 : 0;
	$: volumePercentage = playerState.volume * 100;

	// Available track types for tabs
	$: availableTrackTypes = tracks.map((t) => t.type);

	onMount(() => {
		// Set initial volume from localStorage
		const savedVolume = storage.get('audio_player_volume');
		if (savedVolume) {
			playerState.volume = parseFloat(savedVolume);
		}
	});

	onDestroy(() => {
		if (volumeTimeout) {
			clearTimeout(volumeTimeout);
		}
	});

	// Watch for track changes. Guarded on `loadedUrl` (not just the presence
	// of `currentUrl`/`audioElement`) so a parent re-render can never reload
	// — and reset the playback position of — a track that's already loaded.
	$: if (currentUrl && audioElement && currentUrl !== loadedUrl) {
		loadTrack();
	}

	function loadTrack() {
		if (!audioElement || !currentUrl) return;

		loadedUrl = currentUrl;
		playerState.isLoading = true;
		playerState.error = undefined;

		audioElement.src = currentUrl;
		audioElement.load();

		// Autoplay fires once per loaded track (loadTrack itself only runs
		// once per URL change, see the guard above) rather than reacting to
		// `playerState.isPlaying`, which used to re-trigger play() on every
		// pause.
		if (autoplay) {
			audioElement.addEventListener('canplay', () => playAudio(), { once: true });
		}
	}

	function togglePlay() {
		if (playerState.isPlaying) {
			pauseAudio();
		} else {
			playAudio();
		}
	}

	async function playAudio() {
		if (!audioElement) return;

		try {
			await audioElement.play();
			playerState.isPlaying = true;
			dispatch('play');
		} catch (error) {
			// pause() during a pending play() rejects that promise with
			// AbortError — that's normal user behavior, not a playback failure.
			if (error instanceof DOMException && error.name === 'AbortError') {
				return;
			}

			logger.error('[AudioPlayer] Play error:', error);
			playerState.error = 'Failed to play audio';
			dispatch('error', { error });
		}
	}

	function pauseAudio() {
		if (!audioElement) return;

		audioElement.pause();
		playerState.isPlaying = false;
		dispatch('pause');
	}

	function handleTimeUpdate() {
		if (!audioElement || isDraggingTime) return;

		playerState.currentTime = audioElement.currentTime;
		dispatch('timeupdate', { currentTime: playerState.currentTime });
	}

	function handleLoadedMetadata() {
		if (!audioElement) return;

		playerState.duration = audioElement.duration;
		playerState.isLoading = false;
		dispatch('loadedmetadata', { duration: playerState.duration });
	}

	function handleEnded() {
		playerState.isPlaying = false;
		playerState.currentTime = 0;
		if (audioElement) {
			audioElement.currentTime = 0;
		}
		dispatch('ended');
	}

	function handleError() {
		playerState.isLoading = false;
		playerState.error = 'Failed to load audio file';
		dispatch('error', { error: playerState.error });
	}

	function handleCanPlay() {
		playerState.isLoading = false;
	}

	function handleWaiting() {
		playerState.isLoading = true;
	}

	function seekTo(time: number) {
		if (!audioElement) return;

		audioElement.currentTime = Math.max(0, Math.min(time, playerState.duration));
		playerState.currentTime = audioElement.currentTime;
		dispatch('seek', { time: audioElement.currentTime });
	}

	function handleProgressClick(event: MouseEvent) {
		const target = event.currentTarget as HTMLElement;
		const rect = target.getBoundingClientRect();
		const x = event.clientX - rect.left;
		const percentage = x / rect.width;
		const time = percentage * playerState.duration;

		seekTo(time);
	}

	function handleProgressDragStart(event: MouseEvent) {
		isDraggingTime = true;
		handleProgressDrag(event);
	}

	function handleProgressDrag(event: MouseEvent) {
		if (!isDraggingTime) return;

		const target = document.querySelector('.progress-bar-container') as HTMLElement;
		if (!target) return;

		const rect = target.getBoundingClientRect();
		const x = Math.max(0, Math.min(event.clientX - rect.left, rect.width));
		const percentage = x / rect.width;
		const time = percentage * playerState.duration;

		// Update visual feedback immediately
		playerState.currentTime = time;
	}

	function handleProgressDragEnd(event: MouseEvent) {
		if (!isDraggingTime) return;

		handleProgressDrag(event);
		isDraggingTime = false;

		// Actually seek to the position
		if (audioElement) {
			audioElement.currentTime = playerState.currentTime;
		}
	}

	function handleVolumeChange(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		const volume = parseFloat(target.value);

		playerState.volume = volume;

		if (audioElement) {
			audioElement.volume = volume;
		}

		// Save to localStorage
		storage.set('audio_player_volume', volume.toString());
		dispatch('volumechange', { volume });
	}

	function toggleMute() {
		if (playerState.volume > 0) {
			// Mute
			playerState.volume = 0;
		} else {
			// Unmute to 50%
			playerState.volume = 0.5;
		}

		if (audioElement) {
			audioElement.volume = playerState.volume;
		}

		storage.set('audio_player_volume', playerState.volume.toString());
	}

	function toggleVolumeSlider() {
		showVolumeSlider = !showVolumeSlider;

		// Auto-hide volume slider after 3 seconds
		if (showVolumeSlider) {
			if (volumeTimeout) {
				clearTimeout(volumeTimeout);
			}
			volumeTimeout = setTimeout(() => {
				showVolumeSlider = false;
			}, 3000);
		}
	}

	function selectTrack(trackType: AudioTrackType) {
		if (playerState.selectedTrack === trackType) return;

		const wasPlaying = playerState.isPlaying;
		const currentTime = playerState.currentTime;

		playerState.selectedTrack = trackType;
		playerState.isLoading = true;

		// After track loads, restore playback state
		if (audioElement) {
			audioElement.addEventListener(
				'loadedmetadata',
				() => {
					if (audioElement) {
						audioElement.currentTime = currentTime;
						if (wasPlaying) {
							playAudio();
						}
					}
				},
				{ once: true }
			);
		}

		dispatch('trackchange', { trackType });
	}

	function downloadTrack() {
		if (!currentTrack) return;

		const url = currentTrack.originalUrl || currentTrack.url;
		const link = document.createElement('a');
		link.href = url;
		link.download = `audio_${playerState.selectedTrack}.${currentTrack.format || 'mp3'}`;
		link.click();

		dispatch('download', { trackType: playerState.selectedTrack });
	}

	function formatTime(seconds: number): string {
		if (!isFinite(seconds) || isNaN(seconds)) {
			return '0:00';
		}

		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	// Global mouse event listeners for dragging
	onMount(() => {
		function handleGlobalMouseMove(event: MouseEvent) {
			handleProgressDrag(event);
		}

		function handleGlobalMouseUp(event: MouseEvent) {
			handleProgressDragEnd(event);
		}

		window.addEventListener('mousemove', handleGlobalMouseMove);
		window.addEventListener('mouseup', handleGlobalMouseUp);

		return () => {
			window.removeEventListener('mousemove', handleGlobalMouseMove);
			window.removeEventListener('mouseup', handleGlobalMouseUp);
		};
	});
</script>

<div class="audio-player bg-surface-2 border border-line-strong rounded-lg shadow-sm {className}">
	<!-- Hidden audio element -->
	<audio
		bind:this={audioElement}
		on:timeupdate={handleTimeUpdate}
		on:loadedmetadata={handleLoadedMetadata}
		on:ended={handleEnded}
		on:error={handleError}
		on:canplay={handleCanPlay}
		on:waiting={handleWaiting}
		preload="metadata"
	></audio>

	<!-- Track selection tabs (if multiple tracks) -->
	{#if hasMultipleTracks}
		<div class="border-b border-line">
			<div class="flex">
				{#each availableTrackTypes as trackType}
					<button
						type="button"
						class="flex-1 px-4 py-3 text-sm font-medium transition-colors border-b-2 {playerState.selectedTrack === trackType
							? 'border-signal text-signal bg-signal/10'
							: 'border-transparent text-fg-muted hover:text-fg hover:bg-surface-3'}"
						on:click={() => selectTrack(trackType)}
					>
						<div class="flex items-center justify-center gap-2">
							{#if trackType === 'vocal'}
								<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
									/>
								</svg>
								Vocal
							{:else if trackType === 'instrumental'}
								<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"
									/>
								</svg>
								Instrumental
							{:else}
								<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"
									/>
								</svg>
								Mixed
							{/if}
						</div>
					</button>
				{/each}
			</div>
		</div>
	{/if}

	<!-- Main player content -->
	<div class="p-4 space-y-4">
		<!-- Waveform visualization -->
		{#if showWaveform && currentUrl}
			<Waveform {audioElement} url={currentUrl} onSeek={seekTo} />
		{/if}

		<!-- Progress bar (if no waveform) -->
		{#if !showWaveform}
			<div class="progress-bar-container relative h-2 bg-surface-3 rounded-full cursor-pointer group">
				<div
					class="absolute inset-0 rounded-full overflow-hidden"
					on:click={handleProgressClick}
					on:mousedown={handleProgressDragStart}
					role="slider"
					aria-label="Seek audio"
					aria-valuenow={playerState.currentTime}
					aria-valuemin={0}
					aria-valuemax={playerState.duration}
					tabindex="0"
				>
					<div
						class="h-full bg-signal transition-all duration-100"
						style="width: {progressPercentage}%"
					></div>
				</div>

				<!-- Seek handle -->
				<div
					class="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-signal-solid rounded-full shadow-md opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
					style="left: calc({progressPercentage}% - 8px)"
				></div>
			</div>
		{/if}

		<!-- Controls and time -->
		<div class="flex items-center gap-4">
			<!-- Play/Pause button -->
			<button
				type="button"
				class="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-accent hover:bg-accent-hover text-accent-contrast rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
				on:click={togglePlay}
				disabled={playerState.isLoading || !currentUrl}
				aria-label={playerState.isPlaying ? 'Pause' : 'Play'}
			>
				{#if playerState.isLoading}
					<div class="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
				{:else if playerState.isPlaying}
					<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
						<path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
					</svg>
				{:else}
					<svg class="w-5 h-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
						<path d="M8 5v14l11-7z" />
					</svg>
				{/if}
			</button>

			<!-- Time display -->
			<div class="flex-shrink-0 flex items-center gap-1 text-sm font-mono text-fg">
				<span>{formattedCurrentTime}</span>
				<span class="text-fg-subtle">/</span>
				<span class="text-fg-muted">{formattedDuration}</span>
			</div>

			<!-- Spacer -->
			<div class="flex-1"></div>

			<!-- Volume control -->
			<div class="relative flex items-center gap-2">
				<button
					type="button"
					class="flex-shrink-0 w-8 h-8 flex items-center justify-center text-fg-muted hover:text-fg hover:bg-surface-3 rounded transition-colors"
					on:click={toggleVolumeSlider}
					aria-label="Volume"
				>
					{#if playerState.volume === 0}
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2"
							/>
						</svg>
					{:else if playerState.volume < 0.5}
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"
							/>
						</svg>
					{:else}
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"
							/>
						</svg>
					{/if}
				</button>

				<!-- Volume slider (appears on hover/click) -->
				{#if showVolumeSlider}
					<div
						class="absolute bottom-full right-0 mb-2 bg-surface-2 border border-line-strong rounded-lg shadow-lg p-3"
						role="group"
						aria-label="Volume control"
						on:mouseenter={() => {
							if (volumeTimeout) clearTimeout(volumeTimeout);
						}}
						on:mouseleave={() => {
							volumeTimeout = setTimeout(() => {
								showVolumeSlider = false;
							}, 1000);
						}}
					>
						<input
							type="range"
							min="0"
							max="1"
							step="0.01"
							value={playerState.volume}
							on:input={handleVolumeChange}
							class="w-24 h-2 bg-surface-3 rounded-lg appearance-none cursor-pointer accent-blue-500"
							aria-label="Volume level"
						/>
					</div>
				{/if}
			</div>

			<!-- Download button -->
			{#if showDownload && currentTrack}
				<button
					type="button"
					class="flex-shrink-0 w-8 h-8 flex items-center justify-center text-fg-muted hover:text-fg hover:bg-surface-3 rounded transition-colors"
					on:click={downloadTrack}
					title="Download {playerState.selectedTrack} track"
					aria-label="Download {playerState.selectedTrack} track"
				>
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
						/>
					</svg>
				</button>
			{/if}
		</div>

		<!-- Track metadata -->
		{#if currentTrack}
			<div class="flex items-center gap-4 text-xs text-fg-muted border-t border-line pt-3">
				{#if currentTrack.duration}
					<div class="flex items-center gap-1">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
							/>
						</svg>
						<span>{formatTime(currentTrack.duration)}</span>
					</div>
				{/if}

				{#if currentTrack.sample_rate}
					<div class="flex items-center gap-1">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"
							/>
						</svg>
						<span>{(currentTrack.sample_rate / 1000).toFixed(1)} kHz</span>
					</div>
				{/if}

				{#if currentTrack.format}
					<div class="flex items-center gap-1">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
							/>
						</svg>
						<span>{currentTrack.format.toUpperCase()}</span>
					</div>
				{/if}

				{#if currentTrack.file_size}
					<div class="flex items-center gap-1">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"
							/>
						</svg>
						<span>{(currentTrack.file_size / (1024 * 1024)).toFixed(2)} MB</span>
					</div>
				{/if}
			</div>
		{/if}

		<!-- Error message -->
		{#if playerState.error}
			<Alert variant="danger" icon="warning" density="compact" live="polite">{playerState.error}</Alert>
		{/if}
	</div>
</div>

<style>
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.animate-spin {
		animation: spin 1s linear infinite;
	}

	/* Custom range slider styling */
	input[type='range'] {
		-webkit-appearance: none;
		appearance: none;
	}

	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: #3b82f6;
		cursor: pointer;
		border: 2px solid white;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}

	input[type='range']::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: #3b82f6;
		cursor: pointer;
		border: 2px solid white;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}

	input[type='range']::-webkit-slider-runnable-track {
		height: 8px;
		background: #e5e7eb;
		border-radius: 4px;
	}

	input[type='range']::-moz-range-track {
		height: 8px;
		background: #e5e7eb;
		border-radius: 4px;
	}
</style>
