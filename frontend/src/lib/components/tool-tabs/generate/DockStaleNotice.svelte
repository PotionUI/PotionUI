<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';

	let { onredo, noun = 'picture' }: { onredo: () => void; noun?: 'picture' | 'clip' } = $props();
</script>

<p class="m-0 flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded bg-warning/10 pl-2.5 pr-1 text-sm text-fg" role="alert" data-dock-stale>
	<Icon name="warning" className="h-3.5 w-3.5 flex-none text-warning" />
	<span class="min-w-0 flex-1 truncate"><b class="font-semibold">The {noun} changed.</b> <span class="text-fg-muted">Redo on the new {noun}</span></span>
	<Button variant="secondary" size="xs" icon="refresh" onclick={onredo} attrs={{ 'data-dock-redo': '' }}>Redo</Button>
</p>
