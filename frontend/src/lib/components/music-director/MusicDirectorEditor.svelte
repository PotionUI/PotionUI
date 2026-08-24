<script lang="ts">
	// Top-level Music Director editor: modeless -- there is no mode switch.
	// `mode` on the document is a derived read of its structure plus the
	// "Instrumental (no vocals)" toggle (deriveMusicDirectorMode), kept
	// coherent on every edit so the wire contract and chat tooling (which
	// still key off it) see the truth -- same precedent as Video Director's
	// VideoDirectorEditor.svelte. The reference pool (style, or song/director
	// when the preset also declares style) and extend/repaint media wells all
	// render off CAPABILITY, not off the current derived mode -- gating a
	// well on the very mode only picking a source would produce is a
	// chicken-and-egg dead end.
	//
	// Song sections are a REAL segment list (`MusicDirectorValue.segments`),
	// edited by the shared `SegmentedPromptEditor` -- this editor owns no
	// bespoke section rail/card UI, only the quick-add kind strip wrapped
	// around it. When `capabilities.formOwnsSettings` is true the preset's
	// dynamic form owns duration/instrumental/style description as plain
	// fields, so this editor renders neither -- see
	// content/presets/marketplace/MiniMax-Music3/preset.yml's
	// `vars.music_director.form_owns_settings`.
	import { untrack } from 'svelte';
	import type { MediaRef } from '$lib/types/tabs';
	import type { Segment } from '$lib/types/segments';
	import type { MusicDirectorCapabilities, MusicDirectorValue } from '$lib/types/musicDirector';
	import { SECTION_KINDS } from '$lib/types/musicDirector';
	import {
		normalizeMusicDirectorValue,
		deriveMusicDirectorMode,
		musicReferencesGate,
		mintReferenceId,
		appendQuickSection,
		addMusicReference,
		removeMusicReference
	} from '$lib/utils/musicDirector';
	import { sectionKindColor, sectionKindLabel } from './sectionKindStyle';
	import ReferencePool from './ReferencePool.svelte';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import { Input, Switch } from '$lib/components/ui';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';

	let { value, capabilities, onChange }: {
		value: MusicDirectorValue | undefined;
		capabilities: MusicDirectorCapabilities;
		onChange: (v: MusicDirectorValue) => void;
	} = $props();

	let doc = $state(normalizeMusicDirectorValue(value, capabilities));
	let lastEmitted: MusicDirectorValue = untrack(() => doc);

	$effect(() => {
		if (lastEmitted && JSON.stringify(value) === JSON.stringify(lastEmitted)) return;
		const next = normalizeMusicDirectorValue(value, capabilities);
		doc = next;
		lastEmitted = next;
	});

	$effect(() => {
		const derived = deriveMusicDirectorMode(doc, capabilities);
		if (doc.mode !== derived) {
			doc = { ...doc, mode: derived };
			return;
		}
		if (JSON.stringify(doc) === JSON.stringify(lastEmitted)) return;
		lastEmitted = doc;
		onChange(doc);
	});

	function update(patch: Partial<MusicDirectorValue>) {
		doc = { ...doc, ...patch };
	}

	let derivedMode = $derived(deriveMusicDirectorMode(doc, capabilities));
	let cap = $derived(capabilities.modes[derivedMode]);
	let refGate = $derived(musicReferencesGate(derivedMode, capabilities));
	// Capability-driven, not derived-mode-driven: `hasSongStructure`/
	// `showReferencePool` gate the ONLY entry points that let a fresh
	// document ever reach 'song' / 'director' / 'style' in the first place
	// (deriveMusicDirectorMode requires section/reference content to already
	// exist before it derives to any of them) -- gating the segment editor
	// or pool on the very mode adding content to them would produce would be
	// a dead end no click could ever open.
	let hasSongStructure = $derived(capabilities.enabledModes.includes('song') || capabilities.enabledModes.includes('director'));
	let showReferencePool = $derived(refGate.allowed || capabilities.enabledModes.includes('style'));
	let extendAllowed = $derived(capabilities.enabledModes.includes('extend'));
	let repaintAllowed = $derived(capabilities.enabledModes.includes('repaint'));

	function handleSegmentsChange(segments: Segment[]) {
		update({ segments });
	}

	function addReference(media: MediaRef) {
		const id = mintReferenceId(doc.references);
		update({ references: addMusicReference(doc.references, id, media) });
	}

	function removeReference(id: string) {
		update({ references: removeMusicReference(doc.references, id) });
	}

	function formatDuration(seconds: number): string {
		const s = Math.max(0, Math.round(seconds));
		return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
	}
</script>

<section class="music-director space-y-4" aria-label="Music Director">
	<header class="flex flex-wrap items-center gap-3 border-b border-line pb-3">
		<h2 class="text-lg font-semibold leading-tight text-fg">Music Director</h2>
		{#if derivedMode === 'director' && cap?.compile === 'single_shot'}
			<span class="font-mono text-2xs text-fg-subtle">
				<!-- doc.settings.duration is stale when the dynamic form owns it -->
				{#if capabilities.formOwnsSettings}
					compiles to one generation
				{:else}
					compiles to one {formatDuration(doc.settings.duration)} generation
				{/if}
			</span>
		{/if}
	</header>

	{#if !capabilities.formOwnsSettings}
		<!-- settings row -->
		<div class="flex flex-wrap items-center gap-4">
			<label class="flex items-center gap-1.5">
				<span class="font-mono text-2xs uppercase text-fg-subtle">Duration</span>
				<input
					type="number"
					min="1"
					class="input w-20 font-mono tabular-nums"
					value={doc.settings.duration}
					oninput={(e) => update({ settings: { ...doc.settings, duration: Number((e.currentTarget as HTMLInputElement).value) || doc.settings.duration } })}
				/>
			</label>
			{#if capabilities.enabledModes.includes('t2m')}
				<div class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">Instrumental (no vocals)</span>
					<Switch checked={doc.instrumental} onchange={(checked) => update({ instrumental: checked })} label="Instrumental (no vocals)" />
				</div>
			{/if}
			{#if capabilities.settings.bpm}
				<label class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">BPM</span>
					<input
						type="number"
						min="1"
						class="input w-20 font-mono tabular-nums"
						value={doc.settings.bpm ?? ''}
						oninput={(e) => {
							const raw = (e.currentTarget as HTMLInputElement).value;
							update({ settings: { ...doc.settings, bpm: raw === '' ? null : Number(raw) } });
						}}
					/>
				</label>
			{/if}
			{#if capabilities.settings.key}
				<label class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">Key</span>
					<Input
						value={doc.settings.key ?? ''}
						oninput={(e: Event) => update({ settings: { ...doc.settings, key: (e.currentTarget as HTMLInputElement).value || null } })}
						class="w-28"
					/>
				</label>
			{/if}
			{#if capabilities.settings.timeSignature}
				<label class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">Time sig.</span>
					<Input
						value={doc.settings.time_signature ?? ''}
						oninput={(e: Event) => update({ settings: { ...doc.settings, time_signature: (e.currentTarget as HTMLInputElement).value || null } })}
						class="w-16"
					/>
				</label>
			{/if}
		</div>

		<!-- global style description -->
		<label class="block space-y-1.5 rounded-lg border border-line-strong bg-canvas p-3 shadow-[inset_0_1px_2px_rgb(0_0_0_/_0.35)]">
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Style description</span>
				<span class="ml-auto font-mono text-2xs text-fg-subtle">applies to the whole song</span>
			</div>
			<textarea
				class="input min-h-[52px] w-full resize-y text-sm"
				placeholder="style & tempo &middot; vocals &middot; arrangement"
				value={doc.description}
				oninput={(e) => update({ description: (e.currentTarget as HTMLTextAreaElement).value })}
			></textarea>
		</label>
	{/if}

	{#if showReferencePool}
		<ReferencePool
			references={doc.references}
			onAdd={addReference}
			onRemove={removeReference}
			maxReferenceSeconds={derivedMode === 'style' ? (cap?.maxReferenceSeconds ?? null) : null}
			label={refGate.required ? 'Style reference (required)' : 'Style reference'}
		/>
	{/if}

	{#if hasSongStructure}
		<div class="space-y-1.5">
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Song structure</span>
			<div class="flex flex-wrap items-center gap-1 rounded border border-line-strong bg-field-bg p-1" role="group" aria-label="Add section">
				{#each SECTION_KINDS as kind (kind)}
					<button
						type="button"
						class="rounded px-2 py-1 font-mono text-2xs text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg"
						style="border-left: 2px solid {sectionKindColor(kind)}"
						onclick={() => update({ segments: appendQuickSection(doc.segments, kind) })}
					>
						+ {sectionKindLabel(kind)}
					</button>
				{/each}
			</div>
			<div class="rounded-lg border border-line-strong bg-canvas p-3 shadow-well">
				<SegmentedPromptEditor
					segments={doc.segments}
					label="Song structure"
					showPreview={false}
					showLibraryActions={true}
					placeholder="Write this section's lyrics…"
					on:segmentsChange={(e) => handleSegmentsChange(e.detail)}
				/>
			</div>
		</div>
	{/if}

	{#if extendAllowed}
		<div class="space-y-1.5">
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Track to extend</span>
			<MediaLoaderField
				name="music_director_extend_source"
				value={doc.extend_source?.media ?? null}
				onChange={(_name, v) => update({ extend_source: v ? { media: v as MediaRef } : null })}
				config={{ accept: ['audio'] }}
				compact
			/>
		</div>
	{/if}

	{#if repaintAllowed}
		<div class="space-y-2">
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">Track to repaint</span>
			<MediaLoaderField
				name="music_director_repaint_source"
				value={doc.repaint.source?.media ?? null}
				onChange={(_name, v) => update({ repaint: { ...doc.repaint, source: v ? { media: v as MediaRef } : null } })}
				config={{ accept: ['audio'] }}
				compact
			/>
			<div class="flex items-center gap-3">
				<label class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">Start</span>
					<input
						type="number"
						min="0"
						class="input w-20 font-mono tabular-nums"
						value={doc.repaint.start}
						oninput={(e) => update({ repaint: { ...doc.repaint, start: Number((e.currentTarget as HTMLInputElement).value) || 0 } })}
					/>
				</label>
				<label class="flex items-center gap-1.5">
					<span class="font-mono text-2xs uppercase text-fg-subtle">End</span>
					<input
						type="number"
						min="0"
						class="input w-20 font-mono tabular-nums"
						value={doc.repaint.end}
						oninput={(e) => update({ repaint: { ...doc.repaint, end: Number((e.currentTarget as HTMLInputElement).value) || 0 } })}
					/>
				</label>
			</div>
		</div>
	{/if}
</section>
