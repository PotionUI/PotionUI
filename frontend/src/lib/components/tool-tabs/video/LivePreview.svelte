<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { BudgetMeter, driftMs } from '$lib/effects/budget';
	import { followFrames } from '$lib/effects/clock';
	import { evaluateDerive, parseDerive, type DeriveNode } from '$lib/effects/derive';
	import type { ChainPlan, EffectPass } from '$lib/effects/plan';
	import { ShaderChain, frameTime } from '$lib/effects/ShaderChain';

	let {
		video,
		after,
		before = null,
		compare = false,
		split = 0.5,
		fps,
		duration,
		sources,
		tables = new Map(),
		baseGain = 1,
		visible = true,
		onunsupported,
		onfailed,
		onlost,
		oncap,
		onslow
	}: {
		video: HTMLVideoElement | null;
		after: ChainPlan;
		before?: ChainPlan | null;
		compare?: boolean;
		split?: number;
		fps: number;
		duration: number;
		sources: ReadonlyMap<string, string>;
		tables?: ReadonlyMap<string, Uint16Array>;
		baseGain?: number;
		visible?: boolean;
		onunsupported?: () => void;
		onfailed?: (tool: string) => void;
		onlost?: () => void;
		oncap?: (cap: number) => void;
		onslow?: (tool: string) => void;
	} = $props();

	let canvas = $state<HTMLCanvasElement>();
	let chain = $state.raw<ShaderChain | null>(null);
	const meter = new BudgetMeter();
	const gains = new Map<string, DeriveNode | null>();
	let gainApplied = false;

	function gainOf(pass: EffectPass, mediaTime: number): number {
		if (!pass.gain) return 1;
		let node = gains.get(pass.gain.expr);
		if (node === undefined) {
			try {
				node = parseDerive(pass.gain.expr);
			} catch {
				node = null;
			}
			gains.set(pass.gain.expr, node);
		}
		if (!node) return 1;
		try {
			const time = frameTime(pass, { mediaTime, sourceDuration: duration, fps }).time;
			const value = Number(evaluateDerive(node, { ...pass.gain.env, time }));
			return Number.isFinite(value) ? Math.max(0, value) : 1;
		} catch {
			return 1;
		}
	}

	function applyGain(mediaTime: number) {
		if (!video) return;
		const passes = after.passes.filter((pass): pass is EffectPass => pass.kind === 'effect' && !!pass.gain);
		if (!passes.length) {
			if (gainApplied) video.volume = Math.max(0, Math.min(1, baseGain));
			gainApplied = false;
			return;
		}
		const product = passes.reduce((value, pass) => value * gainOf(pass, mediaTime), 1);
		video.volume = Math.max(0, Math.min(1, baseGain * product));
		gainApplied = true;
	}

	function render(mediaTime: number) {
		if (!chain || !video || !chain.upload(video)) return;
		chain.draw(after, before, { mediaTime, sourceDuration: duration, fps }, { compare, split });
		applyGain(mediaTime);
	}

	function timing(total: number, passes: { tool: string; ms: number }[]) {
		if (meter.frame(total)) oncap?.(meter.cap);
		for (const pass of passes) if (meter.pass(pass.tool, pass.ms)) onslow?.(pass.tool);
	}

	onMount(() => {
		if (!canvas) return;
		chain = ShaderChain.create(canvas, {
			failed: (tool) => onfailed?.(tool),
			lost: () => onlost?.(),
			restored: () => {
				if (video) render(video.currentTime);
			},
			timing
		});
		if (!chain) onunsupported?.();
	});

	onDestroy(() => {
		chain?.dispose();
		chain = null;
		if (video && gainApplied) video.volume = Math.max(0, Math.min(1, baseGain));
	});

	$effect(() => {
		const current = chain;
		if (!current) return;
		for (const [url, text] of sources) current.setSource(url, text);
		for (const [ref, table] of tables) if (!current.hasLut(ref)) current.setLut(ref, table);
		const el = untrack(() => video);
		if (el) untrack(() => render(el.currentTime));
	});

	$effect(() => {
		const el = video;
		const current = chain;
		if (!el || !current) return;
		const stop = followFrames(el, (tick) => {
			render(tick.mediaTime);
			if (!current.timed && !el.paused && tick.interval > 0) {
				const expected = 1000 / Math.max(1, fps * Math.max(0.0625, el.playbackRate));
				if (meter.frame(driftMs(tick.interval, expected))) oncap?.(meter.cap);
			}
		});
		const redraw = () => render(el.currentTime);
		el.addEventListener('seeked', redraw);
		el.addEventListener('loadeddata', redraw);
		return () => {
			stop();
			el.removeEventListener('seeked', redraw);
			el.removeEventListener('loadeddata', redraw);
		};
	});

	$effect(() => {
		void after;
		void before;
		void compare;
		void split;
		const el = video;
		if (el && chain && el.paused) untrack(() => render(el.currentTime));
	});
</script>

<canvas
	bind:this={canvas}
	class="pointer-events-none absolute inset-0 h-full w-full {visible ? '' : 'invisible'}"
	aria-hidden="true"
	data-live-preview
	data-live-size="{after.size.width}x{after.size.height}"
></canvas>
