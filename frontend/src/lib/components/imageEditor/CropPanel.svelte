<script lang="ts">
	import { Button, Kbd } from '$lib/components/ui';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;
</script>

<div class="flex flex-col gap-3 p-3">
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Crop canvas</p>
	<p class="font-mono text-xs tabular-nums text-fg-muted">
		{#if state.cropRect}
			{state.cropRect.width} × {state.cropRect.height}
		{:else}
			{state.width} × {state.height}
		{/if}
	</p>
	<p class="text-xs leading-relaxed text-fg-subtle">
		Drag the area to keep on the canvas, then apply. Cropping changes the image size.
	</p>
	<div class="flex items-center gap-2">
		<div class="flex-1">
			<Button
				variant="primary"
				size="sm"
				icon="check"
				class="w-full"
				disabled={!state.cropRect}
				onclick={() => session.applyCrop()}
			>
				Apply crop
				<span class="ml-1 max-md:hidden"><Kbd keys="Enter" size="md" /></span>
			</Button>
		</div>
		<Button variant="secondary" size="sm" disabled={!state.cropRect} onclick={() => session.cancelCrop()}>
			Cancel
		</Button>
	</div>
</div>
