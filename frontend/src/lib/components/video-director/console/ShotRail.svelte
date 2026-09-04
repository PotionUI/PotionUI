<script lang="ts">
	// `.rail` from console.html: one shot's own local timeline -- a ruler
	// scaled to the shot's own duration (never the film's), with lanes only
	// for what the profile has (Prompt / Keyframes / Audio), each ending in a
	// fixed `+` column. This is the W1 "one idea per shot" rework of
	// `stage-rail/Rail.svelte` for a single shot instead of the whole film.
	//
	// Geometry/anatomy held literal to console.html; the 1440 container-query
	// variant (gutter 60px, "Keyframes" -> "Kf") is CSS-only, keyed off the
	// `video-director` container name VideoDirectorEditor.svelte already sets.
	import { railTimeFromFraction, type ShotRailModel } from './shotRailModel';
	import type { ConsoleSelection } from './consoleSelection';
	import RailPromptLane from './RailPromptLane.svelte';
	import RailKeyframesLane from './RailKeyframesLane.svelte';
	import RailAudioLane from './RailAudioLane.svelte';
	import RailAddColumn from './RailAddColumn.svelte';

	let {
		shotId,
		rail,
		selection,
		onSelect,
		onAddBeat,
		onAddKeyframe,
		onAddAudio,
		onMoveKeyframe,
		onRemoveKeyframe,
		onResizeBeat
	}: {
		shotId: string;
		rail: ShotRailModel;
		selection: ConsoleSelection;
		onSelect: (sel: ConsoleSelection) => void;
		onAddBeat: (atSeconds: number) => void;
		onAddKeyframe: (atSeconds: number) => void;
		onAddAudio: () => void;
		onMoveKeyframe: (id: string, atSeconds: number) => void;
		onRemoveKeyframe: (id: string) => void;
		onResizeBeat: (id: string, edge: 'start' | 'end', atSeconds: number) => void;
	} = $props();

	let lastMajorTickIndex = $derived(rail.ticks.reduce((acc, t, i) => (t.major ? i : acc), -1));
	let snapFraction = $derived((fraction: number) => railTimeFromFraction(rail, fraction));
</script>

<div class="rail">
	<div class="rail-grid">
		<div class="rail-gutter">
			<div class="ruler-spacer"></div>
			{#if rail.lanes.prompt}
				<div class="lane-lbl" style="height:46px">Prompt</div>
			{/if}
			{#if rail.lanes.keyframes}
				<div class="lane-lbl" style="height:64px">
					<span class="lane-lbl-full">Keyframes</span><span class="lane-lbl-narrow">Kf</span>
				</div>
			{/if}
			{#if rail.lanes.audio}
				<div class="lane-lbl" style="height:32px">Audio</div>
			{/if}
		</div>

		<div class="rail-lanes">
			<div class="ruler2">
				{#each rail.ticks as tick, i (i)}
					{#if tick.major}
						<span class="tmaj" style="left:{tick.atPercent}%{i === lastMajorTickIndex ? ';transform:translateX(-100%)' : ''}"
							>{tick.label}</span
						>
					{:else}
						<span class="tmin" style="left:{tick.atPercent}%"></span>
					{/if}
				{/each}
			</div>

			{#if rail.lanes.prompt}
				<RailPromptLane
					{shotId}
					durationSeconds={rail.durationSeconds}
					beats={rail.lanes.prompt.beats}
					canAdd={rail.lanes.prompt.canAdd}
					addDisabledReason={rail.lanes.prompt.addDisabledReason}
					{selection}
					{onSelect}
					onAdd={onAddBeat}
					{onResizeBeat}
					{snapFraction}
				/>
			{/if}

			{#if rail.lanes.keyframes}
				<RailKeyframesLane
					{shotId}
					durationSeconds={rail.durationSeconds}
					marks={rail.lanes.keyframes.marks}
					canAdd={rail.lanes.keyframes.canAdd}
					{selection}
					{onSelect}
					onAdd={onAddKeyframe}
					onMove={onMoveKeyframe}
					onRemove={onRemoveKeyframe}
					{snapFraction}
				/>
			{/if}

			{#if rail.lanes.audio}
				<RailAudioLane {shotId} clips={rail.lanes.audio.clips} {selection} {onSelect} />
			{/if}
		</div>

		<RailAddColumn
			durationSeconds={rail.durationSeconds}
			prompt={rail.lanes.prompt}
			keyframes={rail.lanes.keyframes}
			audio={rail.lanes.audio}
			{onAddBeat}
			{onAddKeyframe}
			{onAddAudio}
		/>
	</div>
</div>

<style>
	.rail {
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
		background: rgb(var(--canvas));
		padding: 12px 14px;
	}
	.rail-grid {
		display: flex;
	}
	.rail-gutter {
		flex: none;
		width: 76px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.ruler-spacer {
		height: 22px;
	}
	.rail-gutter .lane-lbl {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-muted));
		display: flex;
		align-items: center;
	}
	.lane-lbl-narrow {
		display: none;
	}
	.rail-lanes {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
		position: relative;
	}
	.ruler2 {
		height: 22px;
		position: relative;
		border-bottom: 1px solid rgb(var(--line));
	}
	.ruler2 .tmaj {
		position: absolute;
		bottom: 0;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 9.5px;
		color: rgb(var(--fg-subtle));
		transform: translateX(-50%);
	}
	.ruler2 .tmaj::before {
		content: '';
		position: absolute;
		bottom: 16px;
		left: 50%;
		width: 1px;
		height: 6px;
		background: rgb(var(--line-strong));
		transform: translateX(-0.5px);
	}
	.ruler2 .tmin {
		position: absolute;
		bottom: 16px;
		width: 1px;
		height: 3px;
		background: rgb(var(--line));
	}

	/* 1440 secondary layout (console.html §2): narrower gutter, "Keyframes"
	   shortens to "Kf". Keyed off VideoDirectorEditor.svelte's own
	   `container-name: video-director` on its outer section. */
	@container video-director (max-width: 800px) {
		.rail-gutter {
			width: 60px;
		}
		.lane-lbl-full {
			display: none;
		}
		.lane-lbl-narrow {
			display: inline;
		}
	}
</style>
