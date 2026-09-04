<script lang="ts">
	// Nothing is selected on this shot's local rail (the normal resting
	// state -- selection always resolves to the nearest shot document-wide,
	// never to a specific keyframe/beat/audio clip). Per console.html's H3
	// frame: the stage falls through to the film's Global prompt, muted, with
	// a hint that a prompt beat on the timeline would override it here.
	let { globalPromptText }: { globalPromptText: string } = $props();
</script>

<div class="stage-global">
	<div class="stage-cap" style="margin-bottom:6px">Global prompt</div>
	<div class="txt">{globalPromptText}</div>
	<div class="hint">This shot uses the global prompt here — add a prompt beat on the timeline to override it.</div>
</div>

<style>
	.stage-global {
		padding: 14px;
		border: 1px dashed rgb(var(--line-strong));
		border-radius: 6px;
		background: rgb(var(--canvas));
	}
	.stage-cap {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: rgb(var(--fg-subtle));
	}
	.txt {
		font-size: 13px;
		color: rgb(var(--fg-subtle));
		line-height: 1.6;
	}
	.hint {
		margin-top: 8px;
		font-size: 11.5px;
		color: rgb(var(--fg-disabled));
	}
</style>
