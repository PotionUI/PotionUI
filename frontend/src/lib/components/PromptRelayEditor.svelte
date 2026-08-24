<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import RelayTimeline from './RelayTimeline.svelte';
	import type { PromptRelayValue, PromptRelayTimeline } from '$lib/types/tabs';

	export let value: PromptRelayValue | undefined = undefined;
	export let defaultDuration: number = 5;
	export let defaultFps: number = 24;

	const dispatch = createEventDispatcher<{ change: PromptRelayValue }>();

	function normalize(raw: PromptRelayValue | undefined): PromptRelayValue {
		const tl = (raw && raw.timeline) || ({} as Partial<PromptRelayTimeline>);
		return {
			global_prompt: raw?.global_prompt ?? '',
			timeline: {
				duration: typeof tl.duration === 'number' ? tl.duration : defaultDuration,
				fps: typeof tl.fps === 'number' ? tl.fps : defaultFps,
				segments: Array.isArray(tl.segments) ? tl.segments : [],
				imageSegments: Array.isArray(tl.imageSegments) ? tl.imageSegments : [],
				audioSegments: Array.isArray(tl.audioSegments) ? tl.audioSegments : []
			}
		};
	}

	let val: PromptRelayValue = normalize(value);

	// Re-sync from the external value only when it structurally differs (avoids
	// clobbering local edits on every re-render).
	$: {
		const next = normalize(value);
		if (JSON.stringify(next) !== JSON.stringify(val)) {
			val = next;
		}
	}

	function emit() {
		dispatch('change', val);
	}

	function onGlobalPrompt(e: Event) {
		val = { ...val, global_prompt: (e.target as HTMLTextAreaElement).value };
		emit();
	}

	// RelayTimeline owns the whole timeline object (prompts + images + audio + duration/fps).
	function onTimelineChange(timeline: PromptRelayTimeline) {
		val = { ...val, timeline };
		emit();
	}
</script>

<div class="relay-editor space-y-3">
	<!-- Global prompt -->
	<div>
		<label class="relay-label" for="relay-global-prompt">Global Prompt</label>
		<p class="relay-help">
			Conditions the entire video across every segment — anchor persistent characters,
			objects and scene context. Optional.
		</p>
		<textarea
			id="relay-global-prompt"
			class="relay-textarea"
			rows="2"
			placeholder="e.g. cinematic, a golden retriever, sunny park, shot on 35mm"
			value={val.global_prompt}
			on:input={onGlobalPrompt}
		></textarea>
	</div>

	<!-- Unified multi-track timeline (prompts / images / audio) -->
	<RelayTimeline
		value={val.timeline}
		onChange={onTimelineChange}
		{defaultDuration}
		{defaultFps}
	/>
</div>

<style>
	.relay-label {
		display: block;
		font-size: 12px;
		font-weight: 600;
		color: #e4e4e7;
		margin-bottom: 4px;
	}

	.relay-help {
		font-size: 11px;
		color: #71717a;
		margin: 0 0 6px 0;
	}

	.relay-textarea {
		width: 100%;
		resize: vertical;
		box-sizing: border-box;
		background: #18181b;
		border: 1px solid #3f3f46;
		border-radius: 6px;
		color: #e4e4e7;
		font-size: 13px;
		line-height: 1.5;
		padding: 8px 10px;
		outline: none;
		font-family: inherit;
		transition: border-color 0.15s;
	}

	.relay-textarea:focus {
		border-color: #71717a;
	}
</style>
