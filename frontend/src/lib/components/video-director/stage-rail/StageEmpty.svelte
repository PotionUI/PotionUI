<script lang="ts">
	// Nothing is selected -- either no rail object exists yet, or the
	// selected id no longer resolves against the document (a dangling
	// selection after a removal). Per the design's binding rules an empty
	// stage is never the useful state in practice (selection always resolves
	// to the nearest shot), so this is a defensive fallback rather than a
	// normal reachable screen.
</script>

<div class="flex min-h-[160px] flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-6 py-10 text-center">
	<span class="text-sm text-fg-muted">Nothing selected</span>
	<span class="text-xs text-fg-subtle">Pick a shot, join, keyframe or audio clip on the rail below.</span>
</div>
