<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Switch } from '$lib/components/ui';
	import { formatClock, type VideoGroup, type VideoGroupState } from './videoSync';

	let { group }: { group: VideoGroup } = $props();

	let snapshot = $state<VideoGroupState>({ playing: true, muted: true, synced: true, time: 0, duration: 0 });
	let frame = 0;
	let scrubbing = false;

	function loop() {
		group.tick();
		if (!scrubbing) snapshot = group.state();
		frame = requestAnimationFrame(loop);
	}

	onMount(() => {
		snapshot = group.state();
		frame = requestAnimationFrame(loop);
	});

	onDestroy(() => {
		if (frame) cancelAnimationFrame(frame);
	});

	function refresh() {
		snapshot = group.state();
	}

	function scrub(event: Event) {
		scrubbing = true;
		const value = Number((event.currentTarget as HTMLInputElement).value);
		group.seek(value);
		snapshot = { ...group.state(), time: value };
	}

	function endScrub() {
		scrubbing = false;
		refresh();
	}
</script>

<div
	class="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface-1 px-3 py-2"
	data-testid="video-transport"
>
	<IconButton
		icon={snapshot.playing ? 'pause' : 'play'}
		label={snapshot.playing ? 'Pause all' : 'Play all'}
		variant="secondary"
		onclick={() => {
			group.toggle();
			refresh();
		}}
	/>
	<span class="font-mono text-sm tabular-nums text-fg" data-testid="video-transport-clock">
		{formatClock(snapshot.time)}
		<span class="text-fg-subtle">/ {formatClock(snapshot.duration)}</span>
	</span>
	<input
		type="range"
		class="h-1 min-w-32 flex-1 cursor-pointer accent-[rgb(var(--signal))]"
		aria-label="Seek all clips, in seconds"
		min="0"
		max={Math.max(snapshot.duration, 0.1)}
		step="0.1"
		value={snapshot.time}
		oninput={scrub}
		onchange={endScrub}
	/>
	<button
		type="button"
		class="flex items-center gap-1.5 rounded px-2 py-1 text-sm text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
		aria-pressed={snapshot.muted}
		onclick={() => {
			group.setMuted(!snapshot.muted);
			refresh();
		}}
	>
		<Icon name="audio" className="h-4 w-4" />
		{snapshot.muted ? 'Unmute' : 'Mute'}
	</button>
	<Switch
		label="Synced"
		checked={snapshot.synced}
		onchange={(checked) => {
			group.setSynced(checked);
			refresh();
		}}
	/>
	<span class="text-sm text-fg-muted">Synced</span>
</div>
