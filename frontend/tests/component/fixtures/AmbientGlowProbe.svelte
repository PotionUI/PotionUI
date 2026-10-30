<script lang="ts">
	let { src, kind = 'image', crossfade = 0 }: { src: string | null | undefined; kind?: string; crossfade?: number } = $props();
</script>

<div data-ambient-probe data-kind={kind} data-src={src ?? ''} data-crossfade={crossfade}></div>
