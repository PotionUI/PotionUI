<script lang="ts">
	import { onDestroy } from 'svelte';
	import LookList from '$lib/components/tool-tabs/frame/LookList.svelte';
	import { drawSource } from '$lib/filters/thumbPainter';
	import { StillShader } from '$lib/tool-tabs/image/shaderEffect';
	import { EffectThumbPainter, frameSource } from '$lib/tool-tabs/video/effectThumbs';
	import { tileById } from '$lib/tool-tabs/video/effectTiles';
	import { createFrameGrabber, type FrameGrabber } from '$lib/tool-tabs/video/frameGrabber';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';

	let { host, phone = false }: { host: VideoToolHost; phone?: boolean } = $props();

	let video = $derived(host.video);
	const thumbs = new EffectThumbPainter(new StillShader());
	let tileSource = $state.raw<ImageData | null>(null);
	let grabber: { href: string; grabber: FrameGrabber } | null = null;

	let items = $derived(video.tiles.map((tile) => ({ id: tile.id, label: tile.label, group: tile.group, selected: video.pickedTiles.has(tile.id) })));

	$effect(() => {
		const href = video.url;
		const at = video.playhead;
		if (video.playing || !href) return;
		const controller = new AbortController();
		const timer = setTimeout(() => {
			if (!grabber || grabber.href !== href) {
				grabber?.grabber.dispose();
				grabber = { href, grabber: createFrameGrabber(href, 160) };
			}
			grabber.grabber
				.grab(Math.max(0, at), controller.signal)
				.then((dataUrl) => frameSource(dataUrl, phone ? 112 : 144))
				.then((image) => {
					if (controller.signal.aborted || !image) return;
					thumbs.begin();
					tileSource = image;
				})
				.catch(() => undefined);
		}, 300);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	onDestroy(() => {
		grabber?.grabber.dispose();
		video.hoverTile(null);
	});
</script>

{#snippet thumb(item: { id: string })}
	{@const tile = tileById(video.tiles, item.id)}
	{#if item.id === '__original'}
		<canvas use:drawSource={tileSource} class="block h-full w-full object-cover" aria-hidden="true"></canvas>
	{:else if tile}
		<canvas use:thumbs.draw={{ tile, source: tileSource }} class="block h-full w-full object-cover" aria-hidden="true"></canvas>
	{/if}
{/snippet}

{#if video.ready && items.length}
	<LookList label="Looks" {items} original={{ selected: !video.pickedTiles.size }} {thumb} onpick={(id) => video.pickTile(id)} onpreview={(id) => video.hoverTile(id)} />
{/if}
