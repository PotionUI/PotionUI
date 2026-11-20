<script lang="ts">
	import { Button } from '$lib/components/ui';
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import type { FieldConfig } from '$lib/form/reactions';
	import { CROP_ASPECTS, clampCropRect, cropOutputSize, mirrorCropRect, rotateCropRect, turn } from '$lib/media/cropGeometry';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { readCrop } from '$lib/tool-tabs/video/videoSteps';

	let { host }: { host: VideoToolHost } = $props();

	let video = $derived(host.video);
	let params = $derived(video.panelParams('crop'));
	let crop = $derived(readCrop(params));
	let frame = $derived(video.displaySize);
	let pixels = $derived({
		x: Math.round(crop.rect.x * frame.width),
		y: Math.round(crop.rect.y * frame.height),
		w: Math.round(crop.rect.width * frame.width),
		h: Math.round(crop.rect.height * frame.height)
	});
	let output = $derived(cropOutputSize(crop.rect, frame));

	function box(name: string, title: string, max: number): FieldConfig {
		return { type: 'number_box', name, title, minimum: name === 'w' || name === 'h' ? 2 : 0, maximum: max, step: 1, integer: true, default: 0 } as unknown as FieldConfig;
	}

	let fields = $derived<FieldConfig[]>([
		{ type: 'row', name: 'pair_x_y', configuration: { columns: 2 }, children: [box('x', 'X', frame.width), box('y', 'Y', frame.height)] } as unknown as FieldConfig,
		{ type: 'row', name: 'pair_w_h', configuration: { columns: 2 }, children: [box('w', 'W', frame.width), box('h', 'H', frame.height)] } as unknown as FieldConfig
	]);

	function rotate(quarters: number) {
		video.setParams('crop', { ...params, rect: rotateCropRect(crop.rect, quarters), rotation: turn(crop.rotation, quarters) });
	}

	function flip(axis: 'horizontal' | 'vertical') {
		const key = axis === 'horizontal' ? 'flipH' : 'flipV';
		video.setParams('crop', { ...params, rect: mirrorCropRect(crop.rect, axis), [key]: !crop[key] });
	}

	function typed(values: Record<string, unknown>) {
		const num = (name: 'x' | 'y' | 'w' | 'h') => (typeof values[name] === 'number' && Number.isFinite(values[name] as number) ? (values[name] as number) : pixels[name]);
		const rect = clampCropRect({
			x: num('x') / Math.max(1, frame.width),
			y: num('y') / Math.max(1, frame.height),
			width: num('w') / Math.max(1, frame.width),
			height: num('h') / Math.max(1, frame.height)
		});
		video.setAspect('free');
		video.setParams('crop', { ...params, rect });
	}

	const label = 'font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle';
</script>

<div class="flex flex-col gap-2" data-crop-options>
	<span class={label}>Aspect</span>
	<div class="grid grid-cols-3 gap-1.5" role="group" aria-label="Aspect">
		{#each CROP_ASPECTS as entry (entry.key)}
			<Button variant="secondary" size="sm" class="h-8 font-mono" pressed={video.aspect === entry.key} onclick={() => video.setAspect(entry.key)}>{entry.label}</Button>
		{/each}
	</div>
</div>
<div class="flex flex-col gap-2">
	<span class={label}>Orientation</span>
	<div class="grid grid-cols-4 gap-1.5" role="group" aria-label="Orientation">
		<Button variant="secondary" size="sm" class="h-8" icon="editor-rotate-left" ariaLabel="Rotate left" onclick={() => rotate(-1)} />
		<Button variant="secondary" size="sm" class="h-8" icon="editor-rotate-right" ariaLabel="Rotate right" onclick={() => rotate(1)} />
		<Button variant="secondary" size="sm" class="h-8" icon="flip" ariaLabel="Flip horizontally" pressed={crop.flipH} onclick={() => flip('horizontal')} />
		<Button variant="secondary" size="sm" class="h-8" icon="editor-flip-v" ariaLabel="Flip vertically" pressed={crop.flipV} onclick={() => flip('vertical')} />
	</div>
</div>
<EntryForm {fields} values={pixels} label="Crop box" onValues={(values, info) => (info.commit ? undefined : typed(values))} />
<div class="flex flex-col gap-1.5 text-sm">
	<div class="flex items-center justify-between"><span class="text-fg-muted">Source</span><span class="font-mono tabular-nums text-fg">{video.clip.width}×{video.clip.height}</span></div>
	<div class="flex items-center justify-between"><span class="text-fg-muted">Output</span><span class="font-mono tabular-nums text-success" data-crop-output>{output.width}×{output.height}</span></div>
</div>
<p class="text-sm text-fg-muted">Drag the corners on the picture. The playback shows the crop live.</p>
