<script lang="ts">
	import MediaLoaderField from './form-fields/MediaLoaderField.svelte';
	import type {
		PromptRelayTimeline,
		PromptRelaySegment,
		PromptRelayImageSegment,
		PromptRelayAudioSegment,
		MediaRef
	} from '$lib/types/tabs';
	import { chipIndicatorColor } from '$lib/utils/chipIndicatorColor';
	import {
		clamp,
		sortByStart,
		buildTicks,
		mintId,
		attachDrag,
		totalWidth as computeTotalWidth,
		dragSegment,
		trimSegmentLeft,
		trimSegmentRight,
		neighborBounds,
		findSegmentGap,
		stepZoom,
		isZoomGestureEvent,
		vizSlot,
		DEFAULT_ZOOM,
		RULER_H,
		LANE_H,
		GUTTER_W
	} from './video-director/timelineCore';

	export let value: PromptRelayTimeline;
	export let onChange: (timeline: PromptRelayTimeline) => void;
	export let defaultDuration = 5;
	export let defaultFps = 24;

	// ─── Constants ───────────────────────────────────────────────────────────────
	const MIN_AUDIO_LEN = 0.1;

	// Per-prompt colour: cycles through the colorblind-safe --viz-1..--viz-8
	// series (chipIndicatorColor's slot idea), solid for the border, 15% for the
	// fill — Images/Audio are fixed single categories (warning/success below),
	// not per-item, so they don't need a slot.
	const promptColor = (i: number) => ({
		border: chipIndicatorColor(i),
		bg: `rgb(var(--viz-${vizSlot(i)}) / 0.15)`
	});

	// ─── Defensive normalisation ──────────────────────────────────────────────────
	function normalize(raw: PromptRelayTimeline | undefined): PromptRelayTimeline {
		const r = (raw || {}) as Partial<PromptRelayTimeline>;
		return {
			duration: typeof r.duration === 'number' ? r.duration : defaultDuration,
			fps: typeof r.fps === 'number' ? r.fps : defaultFps,
			segments: Array.isArray(r.segments) ? r.segments : [],
			imageSegments: Array.isArray(r.imageSegments) ? r.imageSegments : [],
			audioSegments: Array.isArray(r.audioSegments) ? r.audioSegments : []
		};
	}

	let tl: PromptRelayTimeline = normalize(value);

	// Re-sync from the external value only when structurally different.
	$: {
		const next = normalize(value);
		if (JSON.stringify(next) !== JSON.stringify(tl)) {
			tl = next;
		}
	}

	// ─── UI state ────────────────────────────────────────────────────────────────
	let zoom = DEFAULT_ZOOM; // pixels per second
	let selected: { type: 'prompt' | 'image' | 'audio'; id: string } | null = null;

	$: selPrompt =
		selected?.type === 'prompt' ? tl.segments.find((s) => s.id === selected!.id) ?? null : null;
	$: selImage =
		selected?.type === 'image'
			? (tl.imageSegments ?? []).find((s) => s.id === selected!.id) ?? null
			: null;
	$: selAudio =
		selected?.type === 'audio'
			? (tl.audioSegments ?? []).find((s) => s.id === selected!.id) ?? null
			: null;

	// ─── Ruler ───────────────────────────────────────────────────────────────────
	$: ticks = buildTicks(tl.duration, zoom);
	$: totalWidth = computeTotalWidth(tl.duration, zoom);

	// ─── Emit ──────────────────────────────────────────────────────────────────────
	function emit(updated: PromptRelayTimeline) {
		tl = updated;
		onChange(updated);
	}

	// ─── Add ───────────────────────────────────────────────────────────────────────
	function addPrompt() {
		const gap = findSegmentGap(sortByStart(tl.segments), tl.duration);
		if (!gap) return;
		const seg: PromptRelaySegment = { id: mintId('seg', tl.segments), ...gap, text: '' };
		selected = { type: 'prompt', id: seg.id };
		emit({ ...tl, segments: sortByStart([...tl.segments, seg]) });
	}
	function addImage() {
		const seg: PromptRelayImageSegment = {
			id: mintId('img', tl.imageSegments ?? []),
			start: 0,
			strength: 1.0,
			media: null
		};
		selected = { type: 'image', id: seg.id };
		emit({ ...tl, imageSegments: [...(tl.imageSegments ?? []), seg] });
	}
	function addAudio() {
		const len = Math.min(Math.max(1, tl.duration), tl.duration);
		const seg: PromptRelayAudioSegment = {
			id: mintId('aud', tl.audioSegments ?? []),
			start: 0,
			trimStart: 0,
			length: len,
			media: null
		};
		selected = { type: 'audio', id: seg.id };
		emit({ ...tl, audioSegments: [...(tl.audioSegments ?? []), seg] });
	}

	// ─── Generic per-collection updater ─────────────────────────────────────────────
	function updateSegments(updated: PromptRelaySegment[]) {
		emit({ ...tl, segments: updated });
	}
	function updateImages(updated: PromptRelayImageSegment[]) {
		emit({ ...tl, imageSegments: updated });
	}
	function updateAudios(updated: PromptRelayAudioSegment[]) {
		emit({ ...tl, audioSegments: updated });
	}
	function patchImage(id: string, patch: Partial<PromptRelayImageSegment>) {
		updateImages((tl.imageSegments ?? []).map((s) => (s.id === id ? { ...s, ...patch } : s)));
	}
	function patchAudio(id: string, patch: Partial<PromptRelayAudioSegment>) {
		updateAudios((tl.audioSegments ?? []).map((s) => (s.id === id ? { ...s, ...patch } : s)));
	}

	// ─── Prompt drag / trim ──────────────────────────────────────────────────────────
	function dragPrompt(e: MouseEvent, id: string) {
		if (e.button !== 0) return;
		e.stopPropagation();
		selected = { type: 'prompt', id };
		const seg = tl.segments.find((s) => s.id === id);
		if (!seg) return;
		const startX = e.clientX;
		const { start: os, end: oe } = seg;
		const move = (ev: MouseEvent) => {
			const { start, end } = dragSegment(os, oe, ev.clientX - startX, zoom, tl.duration);
			updateSegments(tl.segments.map((s) => (s.id === id ? { ...s, start, end } : s)));
		};
		attachDrag(move);
	}
	function trimPromptLeft(e: MouseEvent, id: string) {
		e.stopPropagation();
		selected = { type: 'prompt', id };
		const sorted = sortByStart(tl.segments);
		const seg = sorted.find((s) => s.id === id);
		if (!seg) return;
		const { leftBound } = neighborBounds(sorted, id, tl.duration);
		const startX = e.clientX;
		const os = seg.start;
		const oe = seg.end;
		const move = (ev: MouseEvent) => {
			const start = trimSegmentLeft(os, oe, ev.clientX - startX, zoom, leftBound);
			updateSegments(tl.segments.map((s) => (s.id === id ? { ...s, start } : s)));
		};
		attachDrag(move);
	}
	function trimPromptRight(e: MouseEvent, id: string) {
		e.stopPropagation();
		selected = { type: 'prompt', id };
		const sorted = sortByStart(tl.segments);
		const seg = sorted.find((s) => s.id === id);
		if (!seg) return;
		const { rightBound } = neighborBounds(sorted, id, tl.duration);
		const startX = e.clientX;
		const os = seg.start;
		const oe = seg.end;
		const move = (ev: MouseEvent) => {
			const end = trimSegmentRight(os, oe, ev.clientX - startX, zoom, rightBound);
			updateSegments(tl.segments.map((s) => (s.id === id ? { ...s, end } : s)));
		};
		attachDrag(move);
	}

	// ─── Image marker drag (move start only) ────────────────────────────────────────
	function dragImage(e: MouseEvent, id: string) {
		if (e.button !== 0) return;
		e.stopPropagation();
		selected = { type: 'image', id };
		const seg = (tl.imageSegments ?? []).find((s) => s.id === id);
		if (!seg) return;
		const startX = e.clientX;
		const os = seg.start;
		const move = (ev: MouseEvent) => {
			patchImage(id, { start: clamp(os + (ev.clientX - startX) / zoom, 0, tl.duration) });
		};
		attachDrag(move);
	}

	// ─── Audio drag / trim ───────────────────────────────────────────────────────────
	function dragAudio(e: MouseEvent, id: string) {
		if (e.button !== 0) return;
		e.stopPropagation();
		selected = { type: 'audio', id };
		const seg = (tl.audioSegments ?? []).find((s) => s.id === id);
		if (!seg) return;
		const startX = e.clientX;
		const os = seg.start;
		const move = (ev: MouseEvent) => {
			patchAudio(id, { start: clamp(os + (ev.clientX - startX) / zoom, 0, Math.max(0, tl.duration - seg.length)) });
		};
		attachDrag(move);
	}
	function trimAudioRight(e: MouseEvent, id: string) {
		e.stopPropagation();
		selected = { type: 'audio', id };
		const seg = (tl.audioSegments ?? []).find((s) => s.id === id);
		if (!seg) return;
		const startX = e.clientX;
		const ol = seg.length;
		const move = (ev: MouseEvent) => {
			const nl = clamp(ol + (ev.clientX - startX) / zoom, MIN_AUDIO_LEN, tl.duration - seg.start);
			patchAudio(id, { length: nl });
		};
		attachDrag(move);
	}
	function trimAudioLeft(e: MouseEvent, id: string) {
		e.stopPropagation();
		selected = { type: 'audio', id };
		const seg = (tl.audioSegments ?? []).find((s) => s.id === id);
		if (!seg) return;
		const startX = e.clientX;
		const os = seg.start;
		const ot = seg.trimStart;
		const ol = seg.length;
		const move = (ev: MouseEvent) => {
			const dt = (ev.clientX - startX) / zoom;
			const ns = clamp(os + dt, 0, os + ol - MIN_AUDIO_LEN);
			const applied = ns - os;
			patchAudio(id, { start: ns, trimStart: Math.max(0, ot + applied), length: ol - applied });
		};
		attachDrag(move);
	}

	// ─── Selection / delete ──────────────────────────────────────────────────────────
	function deselectBg(e: MouseEvent) {
		if (e.target === e.currentTarget) selected = null;
	}
	function deleteSelected() {
		if (!selected) return;
		const { type, id } = selected;
		selected = null;
		if (type === 'prompt') updateSegments(tl.segments.filter((s) => s.id !== id));
		else if (type === 'image') updateImages((tl.imageSegments ?? []).filter((s) => s.id !== id));
		else updateAudios((tl.audioSegments ?? []).filter((s) => s.id !== id));
	}

	// ─── Toolbar ────────────────────────────────────────────────────────────────────
	function onDuration(e: Event) {
		const d = Math.max(0.1, parseFloat((e.target as HTMLInputElement).value) || defaultDuration);
		emit({ ...tl, duration: d });
	}
	function onFps(e: Event) {
		const f = Math.max(1, Math.round(parseFloat((e.target as HTMLInputElement).value) || defaultFps));
		emit({ ...tl, fps: f });
	}
	const zoomIn = () => (zoom = stepZoom(zoom, 1));
	const zoomOut = () => (zoom = stepZoom(zoom, -1));
	function onWheel(e: WheelEvent) {
		if (isZoomGestureEvent(e)) {
			e.preventDefault();
			e.deltaY < 0 ? zoomIn() : zoomOut();
		}
	}

	// thumbnail url for an image marker
	function thumb(media: MediaRef | null): string | null {
		if (!media) return null;
		if (media.url) return media.url;
		if (media.relative_path) {
			const fn = media.relative_path.split('/').pop();
			return fn ? `/api/media/uploads/${fn}` : null;
		}
		return null;
	}
	// Chronological index per prompt segment, keyed by segment id (the `{#each}`
	// below is keyed by `seg.id`). `tl` gets reassigned on every reorder/edit; this
	// is a `$:` statement (not a plain function called from `{@const}`) so Svelte's
	// dependency scan sees `tl` directly and recomputes when segments move — a
	// function call hides that read and the color/index would freeze at its
	// pre-reorder value.
	$: promptIndexById = new Map(sortByStart(tl.segments).map((s, idx) => [s.id, idx]));
</script>

<div class="rt">
	<!-- Toolbar -->
	<div class="rt-toolbar">
		<div class="rt-tool-group">
			<label class="rt-tool-field">
				<span>Duration</span>
				<input class="rt-num" type="number" min="0.1" step="0.5" value={tl.duration} on:input={onDuration} />
				<span class="rt-unit">s</span>
			</label>
			<label class="rt-tool-field">
				<span>FPS</span>
				<input class="rt-num" type="number" min="1" step="1" value={tl.fps} on:input={onFps} />
			</label>
		</div>

		<div class="rt-tool-group">
			<button type="button" class="rt-add rt-add--prompt" on:click={addPrompt}>+ Prompt</button>
			<button type="button" class="rt-add rt-add--image" on:click={addImage}>+ Image</button>
			<button type="button" class="rt-add rt-add--audio" on:click={addAudio}>+ Audio</button>
			<div class="rt-zoom">
				<button type="button" class="rt-zoom-btn" on:click={zoomOut} aria-label="Zoom out">−</button>
				<span class="rt-zoom-label">{zoom}px/s</span>
				<button type="button" class="rt-zoom-btn" on:click={zoomIn} aria-label="Zoom in">+</button>
			</div>
		</div>
	</div>

	<!-- Timeline body: fixed gutter + scrollable lanes -->
	<div class="rt-body">
		<div class="rt-gutter" style="width:{GUTTER_W}px">
			<div class="rt-gutter-ruler" style="height:{RULER_H}px"></div>
			<div class="rt-gutter-label" style="height:{LANE_H}px"><span class="rt-dot bg-signal"></span>Prompts</div>
			<div class="rt-gutter-label" style="height:{LANE_H}px"><span class="rt-dot bg-warning"></span>Images</div>
			<div class="rt-gutter-label" style="height:{LANE_H}px"><span class="rt-dot bg-success"></span>Audio</div>
		</div>

		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div class="rt-scroll" on:wheel={onWheel}>
			<!-- Ruler -->
			<div class="rt-ruler" style="width:{totalWidth}px; height:{RULER_H}px">
				{#each ticks as t}
					<div class="rt-tick" style="left:{t.time * zoom}px">
						<span class="rt-tick-label">{t.label}</span>
						<div class="rt-tick-line"></div>
					</div>
				{/each}
				<div class="rt-end" style="left:{tl.duration * zoom}px"></div>
			</div>

			<!-- Prompts lane -->
			<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
			<div class="rt-lane" style="width:{totalWidth}px; height:{LANE_H}px" on:click={deselectBg}>
				{#each tl.segments as seg (seg.id)}
					{@const ci = promptIndexById.get(seg.id) ?? -1}
					{@const c = promptColor(ci)}
					{@const sel = selected?.type === 'prompt' && selected.id === seg.id}
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="rt-bar"
						class:rt-bar--sel={sel}
						style="left:{seg.start * zoom}px; width:{Math.max((seg.end - seg.start) * zoom, 6)}px; background:{c.bg}; border-color:{sel ? c.border : 'rgb(var(--line-strong))'}"
						on:mousedown={(e) => dragPrompt(e, seg.id)}
						title="{seg.start.toFixed(2)}s – {seg.end.toFixed(2)}s"
					>
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<div class="rt-trim rt-trim--l" on:mousedown={(e) => trimPromptLeft(e, seg.id)}></div>
						<span class="rt-bar-label">{seg.text || `Prompt ${ci + 1}`}</span>
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<div class="rt-trim rt-trim--r" on:mousedown={(e) => trimPromptRight(e, seg.id)}></div>
					</div>
				{/each}
				{#if tl.segments.length === 0}<div class="rt-hint">No prompts — “+ Prompt”</div>{/if}
			</div>

			<!-- Images lane -->
			<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
			<div class="rt-lane" style="width:{totalWidth}px; height:{LANE_H}px" on:click={deselectBg}>
				{#each tl.imageSegments ?? [] as seg (seg.id)}
					{@const sel = selected?.type === 'image' && selected.id === seg.id}
					{@const tu = thumb(seg.media)}
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="rt-marker"
						class:rt-marker--sel={sel}
						style="left:{seg.start * zoom}px; border-color:{sel ? 'rgb(var(--warning))' : 'rgb(var(--line-strong))'}"
						on:mousedown={(e) => dragImage(e, seg.id)}
						title="image @ {seg.start.toFixed(2)}s"
					>
						{#if tu}
							<img src={tu} alt="" class="rt-marker-img" draggable="false" />
						{:else}
							<svg class="rt-marker-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
								<rect x="3" y="3" width="18" height="18" rx="2" />
								<circle cx="8.5" cy="8.5" r="1.5" />
								<path d="M21 15l-5-5L5 21" />
							</svg>
						{/if}
					</div>
				{/each}
				{#if (tl.imageSegments ?? []).length === 0}<div class="rt-hint">No images — “+ Image”</div>{/if}
			</div>

			<!-- Audio lane -->
			<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
			<div class="rt-lane rt-lane--last" style="width:{totalWidth}px; height:{LANE_H}px" on:click={deselectBg}>
				{#each tl.audioSegments ?? [] as seg (seg.id)}
					{@const sel = selected?.type === 'audio' && selected.id === seg.id}
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div
						class="rt-bar rt-bar--audio"
						class:rt-bar--sel={sel}
						style="left:{seg.start * zoom}px; width:{Math.max(seg.length * zoom, 6)}px; background:rgb(var(--success) / 0.15); border-color:{sel ? 'rgb(var(--success))' : 'rgb(var(--line-strong))'}"
						on:mousedown={(e) => dragAudio(e, seg.id)}
						title="audio @ {seg.start.toFixed(2)}s · {seg.length.toFixed(2)}s"
					>
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<div class="rt-trim rt-trim--l" on:mousedown={(e) => trimAudioLeft(e, seg.id)}></div>
						<span class="rt-bar-label">{seg.media?.name || 'audio'}</span>
						<!-- svelte-ignore a11y-no-static-element-interactions -->
						<div class="rt-trim rt-trim--r" on:mousedown={(e) => trimAudioRight(e, seg.id)}></div>
					</div>
				{/each}
				{#if (tl.audioSegments ?? []).length === 0}<div class="rt-hint">No audio — “+ Audio”</div>{/if}
			</div>
		</div>
	</div>

	<!-- Inspector -->
	{#if selPrompt}
		{@const ci = promptIndexById.get(selPrompt.id) ?? -1}
		<div class="rt-inspector" style="border-color:rgb(var(--viz-{vizSlot(ci)}) / 0.25)">
			<div class="rt-insp-head">
				<span class="rt-insp-title">Prompt {ci + 1}
					<span class="rt-insp-time">{selPrompt.start.toFixed(2)}s – {selPrompt.end.toFixed(2)}s</span>
				</span>
				<button type="button" class="rt-del" on:click={deleteSelected}>Delete</button>
			</div>
			<textarea
				class="rt-textarea"
				rows="3"
				placeholder="Prompt that controls this time window…"
				value={selPrompt.text}
				on:input={(e) => updateSegments(tl.segments.map((s) => (s.id === selPrompt.id ? { ...s, text: (e.target as HTMLTextAreaElement).value } : s)))}
			></textarea>
		</div>
	{:else if selImage}
		<div class="rt-inspector" style="border-color:rgb(var(--warning) / 0.25)">
			<div class="rt-insp-head">
				<span class="rt-insp-title">Image keyframe
					<span class="rt-insp-time">@ {selImage.start.toFixed(2)}s</span>
				</span>
				<button type="button" class="rt-del" on:click={deleteSelected}>Delete</button>
			</div>
			<div class="rt-insp-grid">
				<div class="rt-insp-media">
					<MediaLoaderField name={selImage.id} value={selImage.media}
						onChange={(_n, v) => patchImage(selImage.id, { media: v as MediaRef | null })}
						config={{ accept: 'image/*' }} compact={true} />
				</div>
				<div class="rt-insp-fields">
					<label class="rt-field"><span>Start (s)</span>
						<input class="rt-num" type="number" min="0" max={tl.duration} step="0.1" value={selImage.start}
							on:input={(e) => patchImage(selImage.id, { start: clamp(parseFloat((e.target as HTMLInputElement).value) || 0, 0, tl.duration) })} />
					</label>
					<label class="rt-field"><span>Strength</span>
						<div class="rt-strength">
							<input type="range" class="rt-slider" min="0" max="1" step="0.01" value={selImage.strength}
								on:input={(e) => patchImage(selImage.id, { strength: clamp(parseFloat((e.target as HTMLInputElement).value), 0, 1) })} />
							<input class="rt-num rt-num--xs" type="number" min="0" max="1" step="0.01" value={selImage.strength}
								on:input={(e) => patchImage(selImage.id, { strength: clamp(parseFloat((e.target as HTMLInputElement).value) || 0, 0, 1) })} />
						</div>
					</label>
				</div>
			</div>
		</div>
	{:else if selAudio}
		<div class="rt-inspector" style="border-color:rgb(var(--success) / 0.25)">
			<div class="rt-insp-head">
				<span class="rt-insp-title">Audio clip
					<span class="rt-insp-time">@ {selAudio.start.toFixed(2)}s · {selAudio.length.toFixed(2)}s</span>
				</span>
				<button type="button" class="rt-del" on:click={deleteSelected}>Delete</button>
			</div>
			<div class="rt-insp-grid">
				<div class="rt-insp-media">
					<MediaLoaderField name={selAudio.id} value={selAudio.media}
						onChange={(_n, v) => patchAudio(selAudio.id, { media: v as MediaRef | null })}
						config={{ accept: 'audio/*' }} compact={true} />
				</div>
				<div class="rt-insp-fields">
					<label class="rt-field"><span>Start (s)</span>
						<input class="rt-num" type="number" min="0" step="0.1" value={selAudio.start}
							on:input={(e) => patchAudio(selAudio.id, { start: Math.max(0, parseFloat((e.target as HTMLInputElement).value) || 0) })} />
					</label>
					<label class="rt-field"><span>Trim start (s)</span>
						<input class="rt-num" type="number" min="0" step="0.1" value={selAudio.trimStart}
							on:input={(e) => patchAudio(selAudio.id, { trimStart: Math.max(0, parseFloat((e.target as HTMLInputElement).value) || 0) })} />
					</label>
					<label class="rt-field"><span>Length (s)</span>
						<input class="rt-num" type="number" min="0.01" step="0.1" value={selAudio.length}
							on:input={(e) => patchAudio(selAudio.id, { length: Math.max(MIN_AUDIO_LEN, parseFloat((e.target as HTMLInputElement).value) || MIN_AUDIO_LEN) })} />
					</label>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.rt {
		border: 1px solid rgb(var(--line));
		border-radius: 8px;
		background: rgb(var(--surface-1));
		overflow: hidden;
	}

	/* Toolbar */
	.rt-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 8px;
		padding: 8px 10px;
		background: rgb(var(--surface-2));
		border-bottom: 1px solid rgb(var(--line));
	}
	.rt-tool-group { display: flex; align-items: center; gap: 8px; }
	.rt-tool-field { display: flex; align-items: center; gap: 5px; font-size: 11px; color: rgb(var(--fg-muted)); }
	.rt-unit { color: rgb(var(--fg-muted)); } /* fg-subtle is banned on surface-2+ (tokens.css); this sits on the toolbar */

	.rt-add {
		padding: 4px 10px;
		font-size: 12px;
		font-weight: 500;
		border-radius: 5px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
		cursor: pointer;
		transition: background 0.1s, border-color 0.1s;
	}
	.rt-add:hover { background: rgb(var(--line-strong)); border-color: rgb(var(--line-hover)); }
	.rt-add--prompt { border-left: 3px solid rgb(var(--signal)); }
	.rt-add--image { border-left: 3px solid rgb(var(--warning)); }
	.rt-add--audio { border-left: 3px solid rgb(var(--success)); }

	.rt-zoom { display: flex; align-items: center; gap: 4px; margin-left: 4px; }
	.rt-zoom-btn {
		width: 22px; height: 22px;
		background: rgb(var(--surface-3)); border: 1px solid rgb(var(--line-strong)); border-radius: 4px;
		color: rgb(var(--fg)); cursor: pointer; font-size: 14px; font-weight: 700; line-height: 1;
		display: flex; align-items: center; justify-content: center;
	}
	.rt-zoom-btn:hover { background: rgb(var(--line-strong)); }
	.rt-zoom-label { font-size: 10px; color: rgb(var(--fg-muted)); min-width: 46px; text-align: center; } /* fg-subtle is banned on surface-2+; this sits on the toolbar */

	/* Body */
	.rt-body { display: flex; }
	.rt-gutter {
		flex-shrink: 0;
		background: rgb(var(--surface-2));
		border-right: 1px solid rgb(var(--line));
	}
	.rt-gutter-ruler { border-bottom: 1px solid rgb(var(--line-strong)); }
	.rt-gutter-label {
		display: flex; align-items: center; gap: 6px;
		padding: 0 10px;
		font-size: 11px; font-weight: 600; color: rgb(var(--fg-muted));
		border-bottom: 1px solid rgb(var(--line));
	}
	.rt-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }

	.rt-scroll { overflow-x: auto; overflow-y: hidden; flex: 1; }

	/* Ruler */
	.rt-ruler { position: relative; background: rgb(var(--surface-2)); border-bottom: 1px solid rgb(var(--line-strong)); min-width: 100%; }
	.rt-tick { position: absolute; top: 0; height: 100%; }
	.rt-tick-label { position: absolute; top: 4px; left: 4px; font-size: 9px; color: rgb(var(--fg-muted)); white-space: nowrap; pointer-events: none; }
	.rt-tick-line { position: absolute; bottom: 0; left: 0; width: 1px; height: 7px; background: rgb(var(--line-strong)); }
	.rt-end { position: absolute; top: 0; bottom: 0; width: 2px; background: rgb(var(--line-hover)); }

	/* Lanes */
	.rt-lane { position: relative; border-bottom: 1px solid rgb(var(--line)); min-width: 100%; background: rgb(var(--surface-1)); }
	.rt-lane--last { border-bottom: none; }
	.rt-hint { position: absolute; inset: 0; display: flex; align-items: center; padding-left: 12px; font-size: 11px; color: rgb(var(--fg-subtle)); pointer-events: none; }

	/* Bars (prompt + audio) */
	.rt-bar {
		position: absolute; top: 6px; bottom: 6px;
		border: 2px solid rgb(var(--line-strong)); border-radius: 5px;
		display: flex; align-items: stretch; overflow: hidden;
		cursor: grab; user-select: none; min-width: 6px;
		transition: border-color 0.1s;
	}
	.rt-bar:active { cursor: grabbing; }
	.rt-bar--sel { box-shadow: 0 0 0 1px currentColor; }
	.rt-bar-label {
		flex: 1; align-self: center; padding: 0 6px;
		font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
		pointer-events: none;
		color: rgb(var(--fg));
	}

	.rt-trim { width: 6px; flex-shrink: 0; cursor: col-resize; background: rgba(255,255,255,0.05); }
	.rt-trim:hover { background: rgba(255,255,255,0.18); }
	.rt-trim--l { border-radius: 3px 0 0 3px; }
	.rt-trim--r { border-radius: 0 3px 3px 0; }

	/* Image markers */
	.rt-marker {
		position: absolute; top: 5px; bottom: 5px;
		width: 36px;
		border: 2px solid rgb(var(--line-strong)); border-radius: 5px;
		overflow: hidden; cursor: grab; user-select: none;
		background: rgb(var(--canvas)); color: rgb(var(--fg-muted));
		display: flex; align-items: center; justify-content: center;
		transition: border-color 0.1s;
	}
	.rt-marker:active { cursor: grabbing; }
	.rt-marker--sel { box-shadow: 0 0 0 1px rgb(var(--warning)); }
	.rt-marker-img { width: 100%; height: 100%; object-fit: cover; pointer-events: none; }
	.rt-marker-icon { width: 18px; height: 18px; pointer-events: none; }

	/* Inspector */
	.rt-inspector { border-top: 1px solid rgb(var(--line)); border-left: 2px solid; padding: 10px 12px; background: rgb(var(--surface-2)); }
	.rt-insp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
	.rt-insp-title { font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 8px; color: rgb(var(--fg)); }
	.rt-insp-time { font-size: 11px; font-weight: 400; color: rgb(var(--fg-muted)); } /* fg-subtle is banned on surface-2+; this sits on the inspector */
	.rt-del {
		display: inline-flex; padding: 3px 9px; font-size: 11px;
		background: transparent; border: 1px solid rgb(var(--line-strong)); border-radius: 4px; color: rgb(var(--danger)); cursor: pointer;
	}
	.rt-del:hover { background: rgb(var(--danger) / 0.12); border-color: rgb(var(--danger)); }

	.rt-insp-grid { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
	.rt-insp-media { flex-shrink: 0; }
	.rt-insp-fields { display: flex; flex-wrap: wrap; gap: 10px; flex: 1; align-items: flex-end; }
	.rt-field { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: rgb(var(--fg-muted)); } /* fg-subtle is banned on surface-2+; this sits on the inspector */
	.rt-strength { display: flex; align-items: center; gap: 6px; }
	.rt-slider { flex: 1; min-width: 90px; accent-color: rgb(var(--signal)); cursor: pointer; }

	.rt-textarea {
		width: 100%; resize: vertical; box-sizing: border-box;
		background: rgb(var(--surface-1)); border: 1px solid rgb(var(--line-strong)); border-radius: 4px;
		color: rgb(var(--fg-muted)); font-size: 12px; line-height: 1.5; padding: 8px 10px; outline: none; font-family: inherit;
	}
	.rt-textarea:focus { border-color: rgb(var(--fg-subtle)); }

	.rt-num {
		width: 84px; box-sizing: border-box;
		background: rgb(var(--surface-1)); border: 1px solid rgb(var(--line-strong)); border-radius: 5px;
		color: rgb(var(--fg-muted)); font-size: 12px; padding: 5px 8px; outline: none;
	}
	.rt-num:focus { border-color: rgb(var(--fg-subtle)); }
	.rt-num--xs { width: 58px; }
</style>
