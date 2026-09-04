<script lang="ts">
	// `.audio-lane` from console.html. No timed insert-cue here -- per the
	// W1 brief, audio only grows through the trailing `+` column
	// (`onAddAudio()` takes no time argument), so this lane is select-only.
	import type { RailAudioClip } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';

	const MIN_WIDTH_PERCENT = 2;
	const BAR_COUNT = 42;

	let {
		shotId,
		clips,
		selection,
		onSelect
	}: {
		shotId: string;
		clips: RailAudioClip[];
		selection: ConsoleSelection;
		onSelect: (sel: ConsoleSelection) => void;
	} = $props();

	function isSelected(id: string): boolean {
		return selection?.shotId === shotId && selection.kind === 'audio' && selection.id === id;
	}

	// Deterministic per-clip pseudo-waveform -- console.html fills its
	// `.wave-mini` stubs with a seeded LCG rather than real audio data
	// (PLAN.md: "waveform = static bar stub as in template"); seeding off the
	// clip id keeps each clip visually distinct instead of identical bars.
	function waveformHeights(seedKey: string): number[] {
		let seed = 7;
		for (let i = 0; i < seedKey.length; i++) seed = (seed + seedKey.charCodeAt(i) * (i + 1)) % 233280 || 7;
		const heights: number[] = [];
		for (let i = 0; i < BAR_COUNT; i++) {
			seed = (seed * 9301 + 49297) % 233280;
			heights.push(3 + Math.round((seed / 233280) * 11));
		}
		return heights;
	}
</script>

<div class="audio-lane">
	{#each clips as clip (clip.id)}
		<button
			type="button"
			class="audio-b {isSelected(clip.id) ? 'selected' : ''}"
			style="left:{clip.startPercent}%;width:{Math.max(MIN_WIDTH_PERCENT, clip.widthPercent)}%"
			onclick={() => onSelect({ shotId, kind: 'audio', id: clip.id })}
		>
			<span class="lbl">{clip.role}</span>
			<div class="wave-mini">
				{#each waveformHeights(clip.id) as h, i (i)}
					<span style="height:{h}px"></span>
				{/each}
			</div>
			<span class="lbl filename">{clip.filename}</span>
		</button>
	{/each}
</div>

<style>
	.audio-lane {
		height: 32px;
		position: relative;
		background: rgb(var(--surface-1) / 0.5);
		border-radius: 4px;
		box-shadow: var(--shadow-well);
	}
	.audio-b {
		position: absolute;
		top: 0;
		bottom: 0;
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 0 9px;
		border-radius: 4px;
		background: rgb(var(--surface-1));
		border: 1px solid rgb(var(--line-strong));
		cursor: pointer;
		overflow: hidden;
		text-align: left;
	}
	/* Not shown in the static template (every example clip spans 100% width,
	   so a selected state never appears) -- extended from `.beat-b.selected`
	   for consistency since ConsoleSelection explicitly supports kind:'audio'. */
	.audio-b.selected {
		border-color: rgb(var(--signal));
		background: linear-gradient(180deg, rgb(var(--signal) / 0.16), rgb(var(--signal) / 0.05));
	}
	.audio-b .lbl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9.5px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted));
		white-space: nowrap;
		flex: none;
	}
	.audio-b .lbl.filename {
		color: rgb(var(--fg-subtle));
		text-transform: none;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.audio-b .wave-mini {
		flex: 1;
		height: 14px;
		display: flex;
		align-items: center;
		gap: 1px;
		overflow: hidden;
	}
	.audio-b .wave-mini span {
		width: 2px;
		background: rgb(var(--fg-disabled));
		border-radius: 1px;
		flex: none;
	}
</style>
