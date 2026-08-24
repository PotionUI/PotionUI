<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import type { WaveformConfig } from '$lib/types/audio';
	import {
		computePeaks,
		resamplePeaks,
		createFlatWaveform,
		PEAK_RESOLUTION,
		PEAK_DECODE_SAMPLE_RATE,
		MAX_DECODE_BYTES,
		type Peaks
	} from '$lib/utils/audioPeaks';

	// Props
	export let audioElement: HTMLAudioElement | null = null;
	// The track URL to decode peaks from. Deliberately a separate prop from
	// `audioElement` rather than reading `audioElement.src` — the element is a
	// mutable object the parent re-passes on every re-render, and reacting off
	// it directly is what used to re-run audio-context setup on every parent
	// state change (see the removed setupAudioContext/analyser wiring below).
	export let url: string = '';
	export let config: Partial<WaveformConfig> = {};
	export let onSeek: ((time: number) => void) | undefined = undefined;

	// Default configuration
	const defaultConfig: WaveformConfig = {
		height: 80,
		waveColor: '#94a3b8', // gray-400
		progressColor: '#3b82f6', // blue-500
		cursorColor: '#1e40af', // blue-800
		backgroundColor: '#f1f5f9', // gray-100
		barWidth: 2,
		barGap: 1,
		barRadius: 2,
		responsive: true
	};

	const mergedConfig: WaveformConfig = { ...defaultConfig, ...config };

	// Component state
	let canvas: HTMLCanvasElement;
	let container: HTMLDivElement;
	let ctx: CanvasRenderingContext2D | null = null;
	let animationFrame: number | null = null;
	// Peaks at a fixed resolution (PEAK_RESOLUTION), decoded once per URL and
	// re-bucketed to the actual bar count on every resize via resamplePeaks —
	// a resize never re-fetches or re-decodes audio.
	let basePeaks: Peaks = createFlatWaveform(PEAK_RESOLUTION);
	let waveformData: number[] = [];
	let isHovering = false;
	let hoverPosition = 0;
	let canvasWidth = 0;
	let canvasHeight = mergedConfig.height;

	// Resize observer
	let resizeObserver: ResizeObserver | null = null;
	let resizeTimeout: ReturnType<typeof setTimeout>;

	// The <audio> element currently wired up with listeners. Tracked
	// separately from the `audioElement` prop so re-attaching only happens
	// when the element actually changes, not on every parent re-render.
	let attachedElement: HTMLAudioElement | null = null;

	// Per-URL peak cache, module-scoped so switching back to a previously
	// decoded track (or a sibling Waveform instance) never re-decodes.
	// 'failed' marks a URL whose decode we already gave up on.
	const peaksCache = new Map<string, Peaks | 'failed'>();

	onMount(() => {
		if (canvas) {
			ctx = canvas.getContext('2d');
			setupResizeObserver();
			updateCanvasSize();
		}
	});

	onDestroy(() => {
		cleanup();
		clearTimeout(resizeTimeout);
		if (resizeObserver) {
			resizeObserver.disconnect();
		}
	});

	function setupResizeObserver() {
		if (!mergedConfig.responsive || !container) return;

		resizeObserver = new ResizeObserver(() => {
			clearTimeout(resizeTimeout);
			resizeTimeout = setTimeout(() => {
				updateCanvasSize();
			}, 150);
		});

		resizeObserver.observe(container);
	}

	function updateCanvasSize() {
		if (!canvas || !container) return;

		const rect = container.getBoundingClientRect();
		canvasWidth = rect.width;
		canvas.width = canvasWidth * window.devicePixelRatio;
		canvas.height = canvasHeight * window.devicePixelRatio;
		canvas.style.width = `${canvasWidth}px`;
		canvas.style.height = `${canvasHeight}px`;

		if (ctx) {
			ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
		}

		recomputeBars();
	}

	function barCount(): number {
		return Math.max(0, Math.floor(canvasWidth / (mergedConfig.barWidth! + mergedConfig.barGap!)));
	}

	function recomputeBars() {
		waveformData = resamplePeaks(basePeaks, barCount());
		drawWaveform();
	}

	// Attach native playback listeners to whichever element is actually
	// current. Guarded so it only runs when the element reference genuinely
	// changes — `audioElement` is an object prop the parent re-passes on
	// every re-render, and reacting to every re-pass (rather than genuine
	// changes) is what previously re-ran Web Audio setup on every pause/play.
	$: if (audioElement !== attachedElement) {
		attachToElement(audioElement);
	}

	// Decode (or resample from cache) whenever the track URL changes. `url`
	// is a plain string, so Svelte only reruns this on an actual value change.
	$: loadWaveform(url);

	function attachToElement(el: HTMLAudioElement | null) {
		detachFromElement();
		attachedElement = el;
		if (!el) return;

		el.addEventListener('play', handlePlay);
		el.addEventListener('pause', handlePause);
		el.addEventListener('ended', handlePause);
		el.addEventListener('timeupdate', handleTimeUpdate);
		el.addEventListener('seeked', handleSeeked);

		if (!el.paused) {
			startProgressLoop();
		} else {
			drawWaveform();
		}
	}

	function detachFromElement() {
		stopProgressLoop();
		if (attachedElement) {
			attachedElement.removeEventListener('play', handlePlay);
			attachedElement.removeEventListener('pause', handlePause);
			attachedElement.removeEventListener('ended', handlePause);
			attachedElement.removeEventListener('timeupdate', handleTimeUpdate);
			attachedElement.removeEventListener('seeked', handleSeeked);
		}
		attachedElement = null;
	}

	function handlePlay() {
		startProgressLoop();
	}

	function handlePause() {
		stopProgressLoop();
		drawWaveform();
	}

	function handleTimeUpdate() {
		// The rAF loop already redraws continuously while playing; this only
		// matters while paused (e.g. a seek that fires timeupdate without play).
		if (animationFrame === null) {
			drawWaveform();
		}
	}

	function handleSeeked() {
		drawWaveform();
	}

	function startProgressLoop() {
		if (animationFrame !== null) return;

		function tick() {
			drawWaveform();
			animationFrame = requestAnimationFrame(tick);
		}

		tick();
	}

	function stopProgressLoop() {
		if (animationFrame !== null) {
			cancelAnimationFrame(animationFrame);
			animationFrame = null;
		}
	}

	async function loadWaveform(targetUrl: string) {
		if (!targetUrl) {
			basePeaks = createFlatWaveform(PEAK_RESOLUTION);
			recomputeBars();
			return;
		}

		const cached = peaksCache.get(targetUrl);
		if (cached) {
			basePeaks = cached === 'failed' ? createFlatWaveform(PEAK_RESOLUTION) : cached;
			recomputeBars();
			return;
		}

		try {
			const response = await fetch(targetUrl);
			if (!response.ok) {
				throw new Error(`Failed to fetch audio for waveform: HTTP ${response.status}`);
			}

			const contentLength = Number(response.headers.get('content-length') ?? 0);
			if (contentLength > MAX_DECODE_BYTES) {
				throw new Error(
					`Audio file too large to decode for waveform (${contentLength} bytes > ${MAX_DECODE_BYTES})`
				);
			}

			const arrayBuffer = await response.arrayBuffer();
			if (arrayBuffer.byteLength > MAX_DECODE_BYTES) {
				throw new Error(
					`Audio file too large to decode for waveform (${arrayBuffer.byteLength} bytes)`
				);
			}

			// An OfflineAudioContext never touches playback — decodeAudioData
			// here is a pure offline decode, independent of the <audio> element.
			// Decoding at a reduced sample rate keeps the resulting Float32
			// buffers (and CPU cost) roughly proportional to that rate rather
			// than the source's native rate, which matters for long clips.
			const offlineCtx = new OfflineAudioContext(1, 1, PEAK_DECODE_SAMPLE_RATE);
			const audioBuffer = await offlineCtx.decodeAudioData(arrayBuffer);

			// The URL may have changed while this decode was in flight.
			if (targetUrl !== url) return;

			const channels: Float32Array[] = [];
			for (let i = 0; i < audioBuffer.numberOfChannels; i++) {
				channels.push(audioBuffer.getChannelData(i));
			}

			const peaks = computePeaks(channels, PEAK_RESOLUTION);
			peaksCache.set(targetUrl, peaks);
			basePeaks = peaks;
		} catch (error) {
			logger.error('[Waveform] Falling back to placeholder waveform:', error);
			peaksCache.set(targetUrl, 'failed');
			if (targetUrl === url) {
				basePeaks = createFlatWaveform(PEAK_RESOLUTION);
			}
		} finally {
			if (targetUrl === url) {
				recomputeBars();
			}
		}
	}

	function drawWaveform() {
		if (!ctx || !canvas || waveformData.length === 0) return;
		const context = ctx;

		// Clear canvas
		context.fillStyle = mergedConfig.backgroundColor;
		context.fillRect(0, 0, canvasWidth, canvasHeight);

		const barWidth = mergedConfig.barWidth!;
		const barGap = mergedConfig.barGap!;
		const barRadius = mergedConfig.barRadius!;
		const progress = audioElement ? audioElement.currentTime / audioElement.duration : 0;

		waveformData.forEach((amplitude, index) => {
			const x = index * (barWidth + barGap);
			const barHeight = amplitude * canvasHeight * 0.8;
			const y = (canvasHeight - barHeight) / 2;

			// Determine bar color based on progress
			const barProgress = index / waveformData.length;
			const isPlayed = barProgress <= progress;

			context.fillStyle = isPlayed ? mergedConfig.progressColor : mergedConfig.waveColor;

			// Draw rounded rectangle
			drawRoundedRect(context, x, y, barWidth, barHeight, barRadius);
		});

		// Draw cursor at current position if hovering
		if (isHovering) {
			context.fillStyle = mergedConfig.cursorColor;
			context.fillRect(hoverPosition, 0, 2, canvasHeight);
		}

		// Draw playback cursor
		if (audioElement && !isNaN(progress)) {
			const cursorX = progress * canvasWidth;
			context.fillStyle = mergedConfig.cursorColor;
			context.fillRect(cursorX, 0, 2, canvasHeight);
		}
	}

	function drawRoundedRect(
		ctx: CanvasRenderingContext2D,
		x: number,
		y: number,
		width: number,
		height: number,
		radius: number
	) {
		ctx.beginPath();
		ctx.moveTo(x + radius, y);
		ctx.lineTo(x + width - radius, y);
		ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
		ctx.lineTo(x + width, y + height - radius);
		ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
		ctx.lineTo(x + radius, y + height);
		ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
		ctx.lineTo(x, y + radius);
		ctx.quadraticCurveTo(x, y, x + radius, y);
		ctx.closePath();
		ctx.fill();
	}

	function handleCanvasClick(event: MouseEvent) {
		if (!audioElement || !canvas) return;

		const rect = canvas.getBoundingClientRect();
		const x = event.clientX - rect.left;
		const percentage = x / rect.width;
		const time = percentage * audioElement.duration;

		if (onSeek) {
			onSeek(time);
		} else if (audioElement) {
			audioElement.currentTime = time;
		}
	}

	function handleCanvasMove(event: MouseEvent) {
		if (!canvas) return;

		const rect = canvas.getBoundingClientRect();
		hoverPosition = event.clientX - rect.left;
		drawWaveform();
	}

	function handleCanvasEnter() {
		isHovering = true;
	}

	function handleCanvasLeave() {
		isHovering = false;
		drawWaveform();
	}

	function cleanup() {
		detachFromElement();
	}
</script>

<div bind:this={container} class="waveform-container w-full">
	<canvas
		bind:this={canvas}
		class="waveform-canvas w-full cursor-pointer rounded"
		on:click={handleCanvasClick}
		on:mousemove={handleCanvasMove}
		on:mouseenter={handleCanvasEnter}
		on:mouseleave={handleCanvasLeave}
		role="slider"
		aria-label="Audio waveform"
		aria-valuenow={audioElement && !isNaN(audioElement.currentTime / audioElement.duration) ? Math.round((audioElement.currentTime / audioElement.duration) * 100) : 0}
		aria-valuemin={0}
		aria-valuemax={100}
		tabindex="0"
	></canvas>
</div>

<style>
	.waveform-container {
		position: relative;
		overflow: hidden;
	}

	.waveform-canvas {
		display: block;
		transition: opacity 0.2s ease;
	}

	.waveform-canvas:hover {
		opacity: 0.9;
	}
</style>
