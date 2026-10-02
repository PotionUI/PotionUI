<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { isVideoCover, presetAltText } from '$lib/utils/presetMedia';

	let {
		presetId,
		presetName,
		cover,
		variant = 'small',
		zoom = true,
		class: className = '',
		onfail
	}: {
		presetId: string;
		presetName: string;
		cover: string;
		variant?: 'small' | 'medium' | 'large';
		zoom?: boolean;
		class?: string;
		onfail?: () => void;
	} = $props();

	const HOST_SELECTOR = '[data-cover-host]';
	const NEAR_VIEW_MARGIN = '200px';

	let root = $state<HTMLDivElement>();
	let videoEl = $state<HTMLVideoElement>();
	let hovered = $state(false);
	let focused = $state(false);
	let mostlyInView = $state(false);
	let nearView = $state(false);
	let touchMode = $state(false);
	let reducedMotion = $state(false);
	let videoFailed = $state(false);
	let stillFailed = $state(false);

	const isRemote = $derived(/^(https?:)?\/\//.test(cover) || cover.startsWith('/'));
	const isVideo = $derived(isVideoCover(cover) && !/[?&]size=/.test(cover));
	const stillUrl = $derived(isRemote ? cover : api.getPresetAssetURL(presetId, cover, variant));
	const videoUrl = $derived(isRemote ? cover : api.getPresetAssetURL(presetId, cover));
	const showVideo = $derived(isVideo && !videoFailed && (!reducedMotion || stillFailed));
	const active = $derived(touchMode ? mostlyInView : hovered || focused);

	function handleStillError() {
		if (isVideo && reducedMotion && !stillFailed) stillFailed = true;
		else onfail?.();
	}

	$effect(() => {
		void cover;
		videoFailed = false;
		stillFailed = false;
	});

	$effect(() => {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
		const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
		const hover = window.matchMedia('(hover: none)');
		reducedMotion = motion.matches;
		touchMode = hover.matches;
		const onMotion = (event: MediaQueryListEvent) => (reducedMotion = event.matches);
		const onHover = (event: MediaQueryListEvent) => (touchMode = event.matches);
		motion.addEventListener?.('change', onMotion);
		hover.addEventListener?.('change', onHover);
		return () => {
			motion.removeEventListener?.('change', onMotion);
			hover.removeEventListener?.('change', onHover);
		};
	});

	$effect(() => {
		if (!showVideo || !root) return;
		const host = root.closest<HTMLElement>(HOST_SELECTOR) ?? root;
		const enter = () => (hovered = true);
		const leave = () => (hovered = false);
		const focusIn = () => (focused = true);
		const focusOut = () => (focused = false);
		host.addEventListener('pointerenter', enter);
		host.addEventListener('pointerleave', leave);
		host.addEventListener('focusin', focusIn);
		host.addEventListener('focusout', focusOut);
		return () => {
			host.removeEventListener('pointerenter', enter);
			host.removeEventListener('pointerleave', leave);
			host.removeEventListener('focusin', focusIn);
			host.removeEventListener('focusout', focusOut);
			hovered = false;
			focused = false;
		};
	});

	$effect(() => {
		if (!showVideo || !root) return;
		if (typeof IntersectionObserver === 'undefined') {
			nearView = true;
			return;
		}
		const observer = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (entry.isIntersecting) {
						nearView = true;
						observer.disconnect();
					}
				}
			},
			{ rootMargin: NEAR_VIEW_MARGIN }
		);
		observer.observe(root);
		return () => observer.disconnect();
	});

	$effect(() => {
		if (!showVideo || !touchMode || !root || typeof IntersectionObserver === 'undefined') {
			mostlyInView = false;
			return;
		}
		const observer = new IntersectionObserver(
			(entries) => {
				const last = entries[entries.length - 1];
				if (last) mostlyInView = last.isIntersecting && last.intersectionRatio >= 0.5;
			},
			{ threshold: [0, 0.5, 1] }
		);
		observer.observe(root);
		return () => {
			observer.disconnect();
			mostlyInView = false;
		};
	});

	$effect(() => {
		const video = videoEl;
		if (!video || !showVideo) return;
		if (active && nearView && !reducedMotion) {
			const started = video.play();
			if (started && typeof started.catch === 'function') started.catch(() => {});
		} else {
			video.pause();
			if (video.currentTime > 0) video.currentTime = 0;
		}
	});
</script>

<div
	bind:this={root}
	class="block h-full w-full overflow-hidden {zoom ? 'media-zoom' : ''} {className}" data-cover-media={showVideo ? 'video' : 'image'}>
	{#if showVideo}
		<video
			bind:this={videoEl}
			class="h-full w-full object-cover"
			src={nearView ? videoUrl : undefined}
			poster={stillUrl}
			muted
			loop
			playsinline
			preload="metadata"
			aria-label={presetAltText(presetName)}
			onerror={() => (videoFailed = true)}
		></video>
	{:else}
		<img
			src={stillUrl}
			alt={presetAltText(presetName)}
			class="h-full w-full object-cover"
			loading="lazy"
			onerror={handleStillError}
		/>
	{/if}
</div>

